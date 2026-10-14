import hashlib, hmac, json, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import jwt, pytest, pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from app import api, telegram, realtime
from app.config import settings, validate_production_settings
from app.db import get_session
from app.models import Order, OrderItem
from app.security import issue_token, decode_token, authorize_request


def employee(role='ADM',org='org-1'):
    return dict(employee_id='employee-1',name='tester',role=role,org_id=org,branch_code='B1')


def headers(role='ADM',org='org-1'):
    return {'Authorization':'Bearer '+issue_token(employee(role,org),courier_code='77')}


@pytest_asyncio.fixture
async def client(session):
    async def override(): yield session
    api.app.dependency_overrides[get_session]=override
    async with AsyncClient(transport=ASGITransport(app=api.app),base_url='http://test') as c: yield c
    api.app.dependency_overrides.pop(get_session,None)


async def test_all_private_routes_reject_anonymous_requests(client):
    public={'/health','/auth/login','/tg/auth','/tg/claim','/tg/shift-employees','/','/{path:path}','/{full_path:path}'}
    for route in api.app.routes:
        if not getattr(route,'methods',None) or route.path in public or route.path.startswith(('/openapi','/docs','/redoc')): continue
        path=route.path
        for name in ['item_id','order_id','zone_id','iiko_order_id','number']: path=path.replace('{'+name+'}','1')
        for method in route.methods-{'HEAD','OPTIONS'}:
            response=await client.request(method,path)
            assert response.status_code in {401,403},(method,path,response.status_code)


async def test_own_branch_reads_but_other_branch_denied(client):
    assert (await client.get('/orders?org_id=org-1',headers=headers())).status_code==200
    assert (await client.get('/orders?org_id=org-2',headers=headers())).status_code==403


@pytest.mark.parametrize('path',['/admin/summary?org_id=org-1','/admin/couriers-locations?branch_code=B1'])
async def test_cook_cannot_read_manager_or_gps_data(client,path):
    assert (await client.get(path,headers=headers('ПИЦЦ'))).status_code==403


async def test_cross_branch_order_cannot_be_written(client,session):
    order=Order(iiko_order_id='foreign',org_id='org-2',number='1');session.add(order);await session.commit()
    r=await client.post(f'/orders/{order.id}/take',headers=headers(),json={'by':'forged'})
    assert r.status_code==404
    await session.refresh(order);assert order.on_way_at is None


async def test_cook_cannot_write_another_kitchen_zone(client,session):
    order=Order(iiko_order_id='own',org_id='org-1',number='2');session.add(order);await session.flush()
    item=OrderItem(order_id=order.id,product_id='x',name='roll',station='rolls');session.add(item);await session.commit()
    assert (await client.post(f'/items/{item.id}/ready',headers=headers('ПИЦЦ'),json={'by':'x'})).status_code==403


async def test_ingestion_secret_cannot_reset_database(client):
    settings.webhook_secret='ingestion-secret'
    assert (await client.post('/admin/reset-analytics?key=ingestion-secret')).status_code==403
    assert (await client.post('/admin/reset-analytics',headers={'X-Admin-Key':'ingestion-secret'})).status_code==403


@pytest.mark.parametrize('path',['/webhooks/iiko/cloud','/ingest/front'])
async def test_unconfigured_webhooks_are_closed(client,path):
    settings.webhook_secret=''
    assert (await client.post(path,json=[] if 'cloud' in path else {})).status_code==403


async def test_my_orders_requires_login_and_own_courier(client):
    assert (await client.get('/tg/my-orders?courier_code=77')).status_code==401
    assert (await client.get('/tg/my-orders?courier_code=88',headers=headers('КР'))).status_code==403


def signed_init(age=0,token='testtoken'):
    fields={'auth_date':str(int(time.time())-age),'user':json.dumps({'id':42})}
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    signature=hmac.new(secret,'\n'.join(f'{k}={v}' for k,v in sorted(fields.items())).encode(),hashlib.sha256).hexdigest()
    return urlencode({**fields,'hash':signature})


@pytest.mark.parametrize('age',[301,86400,-31])
def test_telegram_expired_or_future_data_rejected(age):
    settings.telegram_bot_token='testtoken'
    assert telegram.parse_and_validate(signed_init(age)) is None


def test_telegram_empty_key_and_duplicate_fields_rejected():
    settings.telegram_bot_token=''
    assert telegram.parse_and_validate(signed_init(token='')) is None
    settings.telegram_bot_token='testtoken'
    assert telegram.parse_and_validate(signed_init()+'&auth_date=1') is None
    assert telegram.parse_and_validate(signed_init()) is not None


def test_jwt_has_no_pin_and_checks_expiration():
    token=issue_token(employee(),courier_code='private-pin');claims=decode_token(token)
    assert 'private-pin' not in json.dumps(claims)
    assert claims['sub']=='employee-1' and claims['orgs']==['org-1']
    claims['exp']=datetime.now(timezone.utc)-timedelta(seconds=1)
    with pytest.raises(HTTPException): decode_token(jwt.encode(claims,settings.jwt_secret,algorithm='HS256'))


async def test_socket_rejects_anonymous_and_wrong_room(monkeypatch):
    assert await realtime.connect('anonymous',{},None) is False
    actor=decode_token(issue_token(employee('ПИЦЦ')))
    async def actor_session(sid): return actor
    async def forbidden(*args): pytest.fail('must not enter unauthorized room')
    monkeypatch.setattr(realtime.sio,'get_session',actor_session)
    monkeypatch.setattr(realtime.sio,'enter_room',forbidden)
    assert not (await realtime.join('staff',{'org_id':'org-2','zone':'pizza'}))['ok']
    assert not (await realtime.join('staff',{'org_id':'org-1','zone':'courier'}))['ok']


async def test_tracking_requires_order_scoped_link(client):
    assert (await client.get('/api/track/order-1')).status_code==401
    token=jwt.encode({'iss':'saya-kds','aud':'saya-tracking','order_id':'order-2','exp':datetime.now(timezone.utc)+timedelta(hours=1)},settings.jwt_secret,algorithm='HS256')
    assert (await client.get('/api/track/order-1',params={'token':token})).status_code==401


def test_production_refuses_empty_shared_or_default_keys():
    settings.environment='production'
    with pytest.raises(RuntimeError): validate_production_settings()
    settings.jwt_secret=settings.webhook_secret=settings.admin_api_key='x'*40;settings.telegram_bot_token=''
    with pytest.raises(RuntimeError): validate_production_settings()
    settings.jwt_secret='j'*40;settings.webhook_secret='w'*40;settings.admin_api_key='a'*40;settings.cors_origins=''
    validate_production_settings()


def test_credentials_are_redacted_in_exception_text():
    from app.log_safety import redact
    settings.rms_password='privatepassword';settings.telegram_bot_token='secretbottoken'
    original=f'GET https://rms.test/auth?pass={hashlib.sha1(settings.rms_password.encode()).hexdigest()} https://api.telegram.org/botsecretbottoken/sendMessage'
    result=redact(original)
    assert 'privatepassword' not in result and 'secretbottoken' not in result and 'pass=' not in result


async def test_courier_pin_is_not_exposed_in_order_payload(session):
    order=Order(iiko_order_id="opaque",org_id="org-1",number="1",assigned_courier_code="secret-pin")
    order.items=[]
    session.add(order); await session.commit()
    payload=realtime.order_payload(order)
    assert "secret-pin" not in json.dumps(payload)
    assert len(payload["assigned_courier_code"])==64


async def test_courier_cannot_read_same_pin_in_another_branch(client,session):
    from app.security import courier_ref
    for org in ["org-1","org-2"]:
        session.add(Order(iiko_order_id=org,org_id=org,number=org,assigned_courier_code="77"))
    await session.commit()
    r=await client.get('/tg/my-orders',params={'courier_code':courier_ref('77')},headers=headers('КР'))
    assert r.status_code==200
    assert [o['org_id'] for o in r.json()]==['org-1']


async def test_assembler_can_list_couriers_without_pin(client,monkeypatch):
    from app import rms
    from app.security import courier_ref
    async def branches(): return [{'code':'B1','employees':[{'code':'secret-pin','name':'Courier','role':'КР'}]}]
    async def shifts(branch_code): return []
    monkeypatch.setattr(rms,'branches_with_employees',branches)
    monkeypatch.setattr(rms,'shift_employees_today',shifts)
    r=await client.get('/branches/couriers?branch_code=B1',headers=headers('WR1'))
    assert r.status_code==200
    assert r.json()[0]['code']==courier_ref('secret-pin')
    assert 'secret-pin' not in r.text
    assert (await client.get('/branches/couriers?branch_code=B2',headers=headers('WR1'))).status_code==403


async def test_spa_route_remains_public_after_build(client):
    import os
    import app.main
    if not os.path.isdir(app.main.DIST):
        pytest.skip("Build frontend first to verify the production SPA route")
    r=await client.get('/login')
    assert r.status_code==200
    assert 'text/html' in r.headers['content-type']


@pytest.mark.parametrize('selection,expected', [('same-branch',200),('foreign-branch',422),('raw-pin',422)])
async def test_assignment_validates_opaque_branch_identity(client,session,monkeypatch,selection,expected):
    from app import rms
    from app.security import courier_ref
    async def branches():
        return [{'code':'B1','employees':[{'code':'77','name':'Real courier','role':'КР'}]},
                {'code':'B2','employees':[{'code':'88','name':'Foreign courier','role':'КР'}]}]
    async def branch_for_org(org): return 'B1'
    async def no_cloud_employee(*args): return None
    monkeypatch.setattr(rms,'branches_with_employees',branches)
    monkeypatch.setattr(api,'_branch_code_for_org',branch_for_org)
    monkeypatch.setattr(api._iiko_write,'cloud_courier_id',no_cloud_employee)
    order=Order(iiko_order_id='',org_id='org-1',number='assignment',service_type='DeliveryByCourier')
    order.items=[];session.add(order);await session.commit()
    selected={'same-branch':courier_ref('77'),'foreign-branch':courier_ref('88'),'raw-pin':'77'}[selection]
    r=await client.post(f'/orders/{order.id}/assign-courier',headers=headers('WR1'),
        json={'courier_code':selected,'courier_name':'forged name'})
    assert r.status_code==expected
    if expected==200:
        assert r.json()['assigned_courier_name']=='Real courier'
        assert r.json()['assigned_courier_code']==courier_ref('77')
    else:
        assert order.assigned_courier_code is None
