import 'package:flet/flet.dart';
import 'package:flet/src/widgets/control_inherited_notifier.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

/// A ListView given one extent per child places each child at the sum of the
/// extents before it, below its padding; a child's own height does not move
/// the children after it; the list's scroll extent is the sum of the extents
/// before the far children are built; a list without extents keeps Flutter's
/// layout.
class _Backend extends FletBackend {
  _Backend()
      : super(
          pageUri: Uri.parse('http://localhost/'),
          assetsDir: '',
          multiView: false,
          extensions: const <FletExtension>[],
        );
}

Map<String, dynamic> _row(int id, String text, double height) => {
      '_i': id,
      '_c': 'Container',
      'height': height,
      'content': {'_i': id + 100, '_c': 'Text', 'value': text},
    };

Map<String, dynamic> _list({
  required List<Map<String, dynamic>> controls,
  List<double>? extents,
  double top = 0,
}) =>
    {
      '_i': 1,
      '_c': 'Page',
      'content': {
        '_i': 10,
        '_c': 'ListView',
        'expand': 1,
        'padding': {'top': top, 'left': 0, 'right': 0, 'bottom': 0},
        if (extents != null) 'item_extents': extents,
        'controls': controls,
      },
    };

Future<_Backend> _mount(WidgetTester tester, Map<String, dynamic> snapshot) async {
  final backend = _Backend();
  addTearDown(backend.dispose);
  backend.page.update(snapshot, shouldNotify: true);
  await tester.pumpWidget(ChangeNotifierProvider<FletBackend>.value(
    value: backend,
    child: MaterialApp(
      home: Scaffold(
        body: SizedBox(
          height: 600,
          child: withControlInheritedNotifier(
            backend.page,
            (_) => backend.page.buildWidget('content')!,
          ),
        ),
      ),
    ),
  ));
  await tester.pumpAndSettle();
  return backend;
}

// Rows built beyond the viewport (in the cache) count too.
double _top(WidgetTester tester, String text) =>
    tester.getTopLeft(find.text(text, skipOffstage: false)).dy;

void main() {
  testWidgets('children are placed by the extents sent, below the padding',
      (tester) async {
    await _mount(
      tester,
      _list(
        top: 100,
        extents: [56, 56, 200, 48],
        controls: [
          _row(11, 'row 0', 56),
          _row(12, 'row 1', 56),
          _row(13, 'space', 200),
          _row(14, 'older', 48),
        ],
      ),
    );
    final origin = _top(tester, 'row 0');
    expect(origin, 100); // the leading space is the padding
    expect(_top(tester, 'row 1') - origin, 56);
    expect(_top(tester, 'space') - origin, 112);
    expect(_top(tester, 'older') - origin, 312);
  });

  testWidgets('a child keeps its own height inside the slot its extent gives it',
      (tester) async {
    // Python's extent is a slot: a card measured taller than its estimate is
    // laid out at its natural height (so it can report it) while the next
    // child still starts where the extents say.
    await _mount(
      tester,
      _list(
        extents: [40, 40, 40],
        controls: [
          _row(11, 'a', 90),
          _row(12, 'b', 40),
          _row(13, 'c', 40),
        ],
      ),
    );
    expect(_top(tester, 'b') - _top(tester, 'a'), 40);
    expect(_top(tester, 'c') - _top(tester, 'a'), 80);
    expect(tester.getSize(find.ancestor(of: find.text('a'), matching: find.byType(Container)).first).height, 90);
  });

  testWidgets('a window that slides draws the rows at their new places',
      (tester) async {
    // The first window: rows 0..2 then a spacer for the rest.
    final backend = await _mount(
      tester,
      _list(
        extents: [56, 56, 56, 5432],
        controls: [
          _row(11, 'row 0', 56),
          _row(12, 'row 1', 56),
          _row(13, 'row 2', 56),
          _row(14, 'after', 5432),
        ],
      ),
    );
    expect(_top(tester, 'row 2') - _top(tester, 'row 0'), 112);
    // The window slid to rows 7..9: the padding grew, the children changed.
    final list = backend.controlsIndex.get(10)!;
    list.update({
      'padding': {'top': 392, 'left': 0, 'right': 0, 'bottom': 0},
      'item_extents': [56, 56, 56, 5040],
      'controls': [
        _row(21, 'row 7', 56),
        _row(22, 'row 8', 56),
        _row(23, 'row 9', 56),
        _row(24, 'after', 5040),
      ],
    }, shouldNotify: true);
    await tester.pumpAndSettle();
    final origin = _top(tester, 'row 7');
    expect(origin, 392);
    expect(_top(tester, 'row 8') - origin, 56);
    expect(_top(tester, 'row 9') - origin, 112);
    expect(_top(tester, 'after') - origin, 168);
  });

  testWidgets('the scroll extent is the sum of the extents before the far children are built',
      (tester) async {
    // Forty rows then a tall spacer: from the rows it has laid out Flutter would
    // estimate the list to end a couple of rows after them, and a fling or a
    // scrollbar would stop there until the spacer is built.
    final rows = List.generate(40, (index) => _row(11 + index, 'row $index', 56));
    await _mount(
      tester,
      _list(
        extents: [...List.filled(40, 56.0), 5000, 48],
        controls: [...rows, _row(90, 'after', 5000), _row(91, 'more', 48)],
      ),
    );
    final position = tester.state<ScrollableState>(find.byType(Scrollable)).position;
    expect(position.maxScrollExtent, 40 * 56 + 5000 + 48 - 600);
  });

  testWidgets('without extents the list is laid out as before', (tester) async {
    await _mount(
      tester,
      _list(
        controls: [_row(11, 'a', 30), _row(12, 'b', 70), _row(13, 'c', 30)],
      ),
    );
    expect(_top(tester, 'b') - _top(tester, 'a'), 30);
    expect(_top(tester, 'c') - _top(tester, 'a'), 100);
  });
}
