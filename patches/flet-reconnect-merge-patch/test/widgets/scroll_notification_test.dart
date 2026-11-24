import 'package:flet/flet.dart';
import 'package:flet/src/widgets/control_inherited_notifier.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

/// A ListView's scroll notifications are rate-limited by its interval; the
/// last notification the interval drops is held and sent once the interval
/// has passed, replaced by any later one, so the position a scroll came to
/// rest at always reaches the backend.
class _RecordingBackend extends FletBackend {
  _RecordingBackend()
      : super(
          pageUri: Uri.parse('http://localhost/'),
          assetsDir: '',
          multiView: false,
          extensions: const <FletExtension>[],
        );

  final events = <Map<String, dynamic>>[];

  @override
  void triggerControlEventById(
    int controlId,
    String eventName, [
    dynamic eventData,
  ]) {
    events.add({'target': controlId, 'name': eventName, 'data': eventData});
  }
}

Map<String, dynamic> _row(int id, String text) => {
      '_i': id,
      '_c': 'Container',
      'height': 56.0,
      'content': {'_i': id + 1000, '_c': 'Text', 'value': text},
    };

/// A page whose list reports scrolls at most once per [interval] milliseconds.
Map<String, dynamic> _page(int interval) => {
      '_i': 1,
      '_c': 'Page',
      'content': {
        '_i': 10,
        '_c': 'ListView',
        'expand': 1,
        'on_scroll': true,
        'scroll_interval': interval,
        'controls': List.generate(100, (index) => _row(11 + index, 'row $index')),
      },
    };

Future<_RecordingBackend> _mount(WidgetTester tester, Map<String, dynamic> snapshot) async {
  final backend = _RecordingBackend();
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

List<Map<String, dynamic>> _scrolls(_RecordingBackend backend) =>
    backend.events.where((event) => event['name'] == 'scroll').toList();

double _pixels(WidgetTester tester) =>
    tester.state<ScrollableState>(find.byType(Scrollable)).position.pixels;

void main() {
  testWidgets('a scroll inside the interval is sent once the interval has passed',
      (tester) async {
    // An interval far longer than the test: every notification after the first
    // scroll's falls inside it.
    final backend = await _mount(tester, _page(100000));
    final scrollable = find.byType(Scrollable);

    await tester.drag(scrollable, const Offset(0, -300));
    await tester.pump();
    final first = _scrolls(backend);
    expect(first, isNotEmpty);
    expect(first.last['data']['pixels'], _pixels(tester));
    final rested = _pixels(tester);

    await tester.drag(scrollable, const Offset(0, -300));
    await tester.pump();
    expect(_pixels(tester), greaterThan(rested));
    // Inside the interval: nothing more has been sent yet.
    expect(_scrolls(backend).length, first.length);

    await tester.pump(const Duration(milliseconds: 100001));
    final after = _scrolls(backend);
    expect(after.length, first.length + 1);
    expect(after.last['data']['pixels'], _pixels(tester));
  });

  testWidgets('a later notification replaces the held one', (tester) async {
    final backend = await _mount(tester, _page(100000));
    final scrollable = find.byType(Scrollable);

    await tester.drag(scrollable, const Offset(0, -300));
    await tester.pump();
    final first = _scrolls(backend);

    await tester.drag(scrollable, const Offset(0, -300));
    await tester.pump();
    await tester.drag(scrollable, const Offset(0, -300));
    await tester.pump();
    expect(_scrolls(backend).length, first.length);

    await tester.pump(const Duration(milliseconds: 100001));
    final after = _scrolls(backend);
    // One notification for the two held scrolls: the position they came to rest at.
    expect(after.length, first.length + 1);
    expect(after.last['data']['pixels'], _pixels(tester));
  });

  testWidgets('a held notification is dropped when the list goes away', (tester) async {
    final backend = await _mount(tester, _page(100000));
    final scrollable = find.byType(Scrollable);

    await tester.drag(scrollable, const Offset(0, -300));
    await tester.pump();
    await tester.drag(scrollable, const Offset(0, -300));
    await tester.pump();
    final sent = _scrolls(backend).length;

    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(milliseconds: 100001));
    expect(_scrolls(backend).length, sent);
  });
}
