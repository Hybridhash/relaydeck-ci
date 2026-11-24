import 'package:xterm2/xterm.dart';

/// Capture active-buffer grid text with leading positions, combining marks, wide glyphs and
/// wrapped lines preserved. Trim trailing whitespace and empty lines without truncating
/// oversized truths; allocation budgets are enforced by the receiver.
List<String> snapshotLines(Terminal terminal) {
  final raws = <String>[];
  final current = StringBuffer();
  var started = false;
  for (var i = 0; i < terminal.buffer.lines.length; i++) {
    final line = terminal.buffer.lines[i];
    if (started && !line.isWrapped) {
      raws.add(current.toString());
      current.clear();
    }
    started = true;
    var pad = 0;
    while (pad < line.length && line.getCodePoint(pad) == 0) {
      pad++;
    }
    current.write(' ' * pad);
    current.write(line.getText());
  }
  if (started) raws.add(current.toString());
  final lines = raws.map((l) {
    var end = l.length;
    while (end > 0 && (l[end - 1] == ' ' || l[end - 1] == '\t')) {
      end--;
    }
    return l.substring(0, end);
  }).toList();
  while (lines.isNotEmpty && lines.last.isEmpty) {
    lines.removeLast();
  }
  return lines;
}

/// The frozen sheet document: snapshot lines joined with newlines.
String snapshotText(Terminal terminal) => snapshotLines(terminal).join('\n');
