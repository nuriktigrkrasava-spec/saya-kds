"""Server-side authentication and tenant/role enforcement for every API route."""
from __future__ import annotations
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
import hashlib, hmac, json, time
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_session
from app.models import Order, OrderItem

MANAGERS = frozenset(['MN0', 'Администратор', 'УПРЭКСПО', 'ADM', 'ADM2'])
ADMINS = frozenset(['ADM', 'ADM2'])
COURIERS = frozenset(['КР', 'КЭКСПО20'])
ROLLS = frozenset(['СУШ', 'СЭКСПО12', 'СЭКСПО13', 'СуШеф', 'CO1', 'ПОВ'])
PIZZA = frozenset(['ПИЦЦ', 'ПЭКСПО13'])
ASSEMBLERS = frozenset(['WR1', 'opeCC'])
ROLES = MANAGERS | COURIERS | ROLLS | PIZZA | ASSEMBLERS
MACHINE_ADMIN_PATHS = frozenset(['/webhooks/register', '/admin/branch-mapping', '/tg/set-bot-webhook', '/admin/reset-analytics'])
_login_buckets: OrderedDict[str, deque] = OrderedDict()


def require_secret(supplied: str, expected: str):
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(403, 'Forbidden')


def login_limit(request: Request):
    key = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    bucket = _login_buckets.setdefault(key, deque())
    _login_buckets.move_to_end(key)
    while bucket and now-bucket[0] > 60: bucket.popleft()
    if len(bucket) >= 6: raise HTTPException(429, 'Too many login attempts')
    bucket.append(now)
    while len(_login_buckets) > 5000: _login_buckets.popitem(last=False)


def courier_ref(code: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), ('courier:'+code).encode(), hashlib.sha256).hexdigest()


def issue_token(emp: dict, telegram_id: str = '', courier_code: str = '') -> str:
    if len(settings.jwt_secret) < 32 or settings.jwt_secret == 'dev-only-change-me':
        raise HTTPException(503, 'Authentication is not configured')
    if emp.get('role') not in ROLES or not emp.get('org_id') or not emp.get('employee_id'):
        raise HTTPException(403, 'Employee access is not configured')
    now = datetime.now(timezone.utc)
    scopes = [str(emp['org_id'])]
    # Multi-branch access is an explicit employee-id allowlist, never client supplied.
    extra = json.loads(settings.employee_org_access or '{}').get(emp['employee_id'], [])
    if emp['role'] in MANAGERS: scopes.extend(str(x) for x in extra)
    return jwt.encode({
        **{k:emp.get(k,'') for k in ['name','role','branch_code','branch_name','org_id']},
        'sub':str(emp['employee_id']), 'orgs':sorted(set(scopes)), 'telegram_id':telegram_id,
        'courier_ref':courier_ref(courier_code) if courier_code else '',
        'iss':'saya-kds', 'aud':'saya-kds-client', 'iat':now, 'exp':now+timedelta(hours=16)
    }, settings.jwt_secret, algorithm='HS256')


def decode_token(token: str) -> dict:
    try:
        if len(settings.jwt_secret) < 32: raise ValueError('unconfigured')
        actor = jwt.decode(token, settings.jwt_secret, algorithms=['HS256'], issuer='saya-kds',
            audience='saya-kds-client', options={'require':['sub','iat','exp','orgs','role','org_id']})
        if actor['role'] not in ROLES or not isinstance(actor['orgs'], list) or not actor['org_id']:
            raise ValueError('invalid permissions')
        return actor
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(401, 'Sign in required') from None


def require_org(actor: dict, org_id: str):
    if org_id not in actor['orgs']: raise HTTPException(403, 'Branch access denied')


def allowed_zones(actor: dict) -> set[str]:
    role = actor['role']
    if role in MANAGERS: return {'rolls','hot','pizza','other','assembler','courier'}
    if role in COURIERS: return {'courier'}
    if role in ROLLS: return {'rolls','hot'}
    if role in PIZZA: return {'pizza'}
    if role in ASSEMBLERS: return {'assembler'}
    return set()


async def authorize_request(request: Request, session: AsyncSession = Depends(get_session)):
    path = request.url.path
    # Only the explicitly registered static-file/SPA routes can be public.
    if getattr(request.scope.get("route"), "path", "") in {"/", "/{path:path}", "/{full_path:path}"}: return
    if path in {'/health','/auth/login','/tg/auth','/tg/claim','/tg/shift-employees'}: return
    if path in {'/webhooks/iiko/cloud','/ingest/front'}:
        require_secret(request.query_params.get('key',''), settings.webhook_secret)
        return
    if path == '/tg/bot-webhook':
        require_secret(request.headers.get('x-telegram-bot-api-secret-token',''), settings.telegram_webhook_secret)
        return
    if path in MACHINE_ADMIN_PATHS or path.startswith('/debug/'):
        require_secret(request.headers.get('x-admin-key',''), settings.admin_api_key)
        return
    if path == '/admin/analytics/export':
        require_secret(request.headers.get('x-api-key',''), settings.analytics_api_key)
        return
    if path.startswith('/api/track/'):
        token = request.query_params.get('token','')
        try:
            claims = jwt.decode(token, settings.jwt_secret, algorithms=['HS256'], audience='saya-tracking',
                issuer='saya-kds', options={'require':['exp','order_id']})
            if str(claims['order_id']) != request.path_params['iiko_order_id']: raise ValueError()
        except (jwt.InvalidTokenError, ValueError, KeyError):
            raise HTTPException(401, 'A private tracking link is required') from None
        return
    header = request.headers.get('authorization','')
    if not header.startswith('Bearer '): raise HTTPException(401, 'Sign in required')
    actor = decode_token(header[7:]); request.state.actor = actor
    if path.startswith('/admin/') or path == '/rms/branches':
        if actor['role'] not in MANAGERS: raise HTTPException(403, 'Manager access required')
    if path == '/branches/couriers' and actor['role'] not in MANAGERS | ASSEMBLERS:
        raise HTTPException(403, 'Assembler access required')
    org_id = request.query_params.get('org_id')
    if org_id: require_org(actor, org_id)
    branch = request.query_params.get('branch_code')
    if branch and branch != actor['branch_code']: raise HTTPException(403, 'Branch access denied')
    if path == '/admin/couriers-locations' and not branch:
        raise HTTPException(422, 'branch_code is required')
    if request.method in {'POST','PUT','PATCH'}:
        try: body = await request.json()
        except (ValueError, UnicodeDecodeError): body = {}
        if isinstance(body, dict):
            if body.get('org_id'): require_org(actor, str(body['org_id']))
    if path.startswith('/tg/') and actor['role'] not in COURIERS | MANAGERS:
        raise HTTPException(403, 'Courier access required')
    if path == '/tg/my-orders':
        code = request.query_params.get('courier_code','')
        if not hmac.compare_digest(code, actor.get('courier_ref','')):
            raise HTTPException(403, 'Courier access denied')
    # Route IDs must resolve to an object in an authorized branch, not just a valid integer.
    order_id = request.path_params.get('order_id')
    item_id = request.path_params.get('item_id')
    order = None; item = None
    if item_id is not None:
        try: item = await session.get(OrderItem, int(item_id))
        except ValueError: item = None
        if item: order = await session.get(Order, item.order_id)
    elif order_id is not None:
        try: order = await session.get(Order, int(order_id))
        except ValueError: order = None
    if order_id is not None or item_id is not None:
        if not order or order.org_id not in actor['orgs']: raise HTTPException(404, 'Order not found')
        if item and actor['role'] not in MANAGERS:
            if path.endswith(('/check','/revert')):
                if actor['role'] not in ASSEMBLERS: raise HTTPException(403, 'Assembler access required')
            elif (item.kitchen_zone or item.station or 'other') not in allowed_zones(actor):
                raise HTTPException(403, 'Kitchen zone access denied')
        if not item and request.method != 'GET':
            if path.endswith(('/assemble','/assign-courier')):
                if actor['role'] not in MANAGERS | ASSEMBLERS: raise HTTPException(403,'Assembler access required')
            elif path.endswith('/delivered') and actor['role'] in ASSEMBLERS and order.service_type != 'DeliveryByCourier':
                pass
            elif actor['role'] not in MANAGERS | COURIERS: raise HTTPException(403,'Courier access required')
            if actor['role'] in COURIERS and not hmac.compare_digest(
                courier_ref(order.assigned_courier_code or ''), actor.get('courier_ref','')):
                raise HTTPException(403, 'Courier access denied')
