import 'dart:async';

import 'package:flutter/widgets.dart';

import '../models/control.dart';
import '../utils/numbers.dart';

class ScrollNotificationControl extends StatefulWidget {
  final Widget child;
  final Control control;

  ScrollNotificationControl(
      {Key? key, required this.child, required this.control})
      : super(key: key ?? ValueKey("control_${control.id}"));

  @override
  State<ScrollNotificationControl> createState() =>
      _ScrollNotificationControlState();
}

class _ScrollNotificationControlState extends State<ScrollNotificationControl> {
  int _onScrollInterval = 0;
  final Map<String, int> _lastEventTimestamps = {};
  // RelayDeck: a notification the interval drops is held and sent once the
  // interval has passed, unless a later one is sent first; so the last
  // notification of a burst always arrives, and the reader of the position
  // learns where a wheel scroll (a burst of short scrolls) came to rest.
  String? _heldType;
  Map<String, Object?>? _heldFields;
  Timer? _flush;

  @override
  void dispose() {
    _flush?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    _onScrollInterval = widget.control.getInt("scroll_interval", 10)!;

    return NotificationListener<ScrollNotification>(
      child: widget.child,
      onNotification: (notification) => _onNotification(notification, context),
    );
  }

  bool _onNotification(ScrollNotification notification, BuildContext context) {
    if (notification.depth != 0) return false;

    final eventType = notification.runtimeType.toString();
    final fields = _fields(notification);
    if (fields == null) return false;

    final now = DateTime.now().millisecondsSinceEpoch;
    final lastEventTimestamp = _lastEventTimestamps[eventType];

    if (lastEventTimestamp != null &&
        now - lastEventTimestamp <= _onScrollInterval) {
      _heldType = eventType;
      _heldFields = fields;
      _flush?.cancel();
      _flush = Timer(
          Duration(
              milliseconds: _onScrollInterval - (now - lastEventTimestamp) + 1),
          _sendHeld);
      return false;
    }

    _dropHeld();
    _send(eventType, fields, now);
    return false;
  }

  void _sendHeld() {
    final eventType = _heldType;
    final fields = _heldFields;
    _dropHeld();
    if (eventType == null || fields == null || !mounted) return;
    _send(eventType, fields, DateTime.now().millisecondsSinceEpoch);
  }

  void _dropHeld() {
    _flush?.cancel();
    _flush = null;
    _heldType = null;
    _heldFields = null;
  }

  void _send(String eventType, Map<String, Object?> fields, int now) {
    _lastEventTimestamps[eventType] = now;
    debugPrint("ScrollNotification ${widget.control.id} event");
    widget.control.triggerEvent("scroll", fields);
  }

  Map<String, Object?>? _fields(ScrollNotification notification) {
    final metrics = notification.metrics;
    final Map<String, Object?> fields = {
      "pixels": metrics.pixels,
      "min_scroll_extent": metrics.minScrollExtent,
      "max_scroll_extent": metrics.maxScrollExtent,
      "viewport_dimension": metrics.viewportDimension,
    };

    if (notification is ScrollStartNotification) {
      fields["event_type"] = "start";
    } else if (notification is ScrollUpdateNotification) {
      fields["event_type"] = "update";
      fields["scroll_delta"] = notification.scrollDelta;
    } else if (notification is ScrollEndNotification) {
      fields["event_type"] = "end";
    } else if (notification is UserScrollNotification) {
      fields["event_type"] = "user";
      fields["direction"] = notification.direction.name;
    } else if (notification is OverscrollNotification) {
      fields["event_type"] = "overscroll";
      fields["overscroll"] = notification.overscroll;
      fields["velocity"] = notification.velocity;
    } else {
      return null;
    }
    return fields;
  }
}
