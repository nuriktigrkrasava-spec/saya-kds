// Saya KDS — перехват кухонной печати iikoFront.
// iikoFront печатает марки/чеки кухни на сетевой принтер (raw ESC/POS, порт 9100).
// Эта утилита поднимает TCP-слушатель, принимает задание печати, очищает от
// управляющих байтов ESC/POS и:
//   1) ДАМПИТ читаемый текст в консоль и в файл capture.txt  (шаг 1 — снять формат),
//   2) (после того как увидим формат) парсит в заказ и шлёт в /ingest/front.
//
// Запуск:  dotnet run            (слушает 0.0.0.0:9100)
//          dotnet run -- 9101    (другой порт)
//
// В iikoOffice печать кухонной группы настраивается на IP этого ПК и порт 9100.

using System.Net;
using System.Net.Sockets;
using System.Text;

int port = args.Length > 0 && int.TryParse(args[0], out var p) ? p : 9100;

// Кодировки кириллицы для ESC/POS (cp866 — частый дефолт чековых принтеров).
Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
var encodings = new[] { "cp866", "windows-1251", "utf-8" };

var listener = new TcpListener(IPAddress.Any, port);
listener.Start();
Console.WriteLine($"[SayaPrint] слушаю порт {port}. Сделай тестовую кухонную печать в iikoFront.");
Console.WriteLine($"[SayaPrint] IP этого ПК укажи в iikoOffice как адрес принтера, порт {port}.");

while (true)
{
    using var client = await listener.AcceptTcpClientAsync();
    using var stream = client.GetStream();
    using var ms = new MemoryStream();
    var buf = new byte[4096];
    int n;
    // Читаем задание целиком (до закрытия соединения / паузы).
    client.ReceiveTimeout = 3000;
    try
    {
        while ((n = await stream.ReadAsync(buf)) > 0)
            ms.Write(buf, 0, n);
    }
    catch { /* таймаут чтения = конец задания */ }

    var raw = ms.ToArray();
    if (raw.Length == 0) continue;

    string text = Decode(raw, encodings);
    string clean = CleanEscPos(text);

    string stamp = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
    string block = $"\n===== PRINT JOB {stamp}  ({raw.Length} bytes) =====\n{clean}\n===== END =====\n";
    Console.WriteLine(block);
    await File.AppendAllTextAsync("capture.txt", block, Encoding.UTF8);
    Console.WriteLine("[SayaPrint] задание записано в capture.txt — пришли его содержимое для разбора формата.");
}

// Подбираем кодировку: берём ту, где меньше «мусорных» символов.
static string Decode(byte[] raw, string[] encs)
{
    string best = ""; int bestScore = int.MaxValue;
    foreach (var e in encs)
    {
        try
        {
            var s = Encoding.GetEncoding(e).GetString(raw);
            int bad = s.Count(ch => ch == '�');
            if (bad < bestScore) { bestScore = bad; best = s; }
        }
        catch { }
    }
    return best;
}

// Убираем ESC/POS-команды и непечатаемые байты, оставляем текст и переносы строк.
static string CleanEscPos(string s)
{
    var sb = new StringBuilder();
    foreach (var ch in s)
    {
        if (ch == '\n' || ch == '\t') { sb.Append(ch); continue; }
        if (ch == '\r') continue;
        if (ch < 0x20) continue;            // управляющие/ESC/GS и т.п.
        if (ch == '�') continue;       // нерасшифрованное
        sb.Append(ch);
    }
    // схлопываем пустые строки
    var lines = sb.ToString().Split('\n').Select(l => l.TrimEnd());
    return string.Join("\n", lines.Where((l, i) => l.Length > 0 || true));
}
