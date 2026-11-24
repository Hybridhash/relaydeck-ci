import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import 'picture_paste.dart';

/// Observe browser paste without changing layout or focus, retaining image Blobs for approved
/// upload or abort. Terminal text follows the application paste event; text fields receive
/// native insertion.
class RelayDeckPicturePasteControl extends StatefulWidget {
  const RelayDeckPicturePasteControl({super.key, required this.control});

  final Control control;

  @override
  State<RelayDeckPicturePasteControl> createState() =>
      _RelayDeckPicturePasteState();
}

class _RelayDeckPicturePasteState extends State<RelayDeckPicturePasteControl> {
  final FocusNode _focus = FocusNode(
    debugLabel: 'picture-paste',
    canRequestFocus: false,
    skipTraversal: true,
  );
  void Function()? _unregister;
  final Set<String> _offered = {};

  String get _owner => widget.control.id.toString();

  @override
  void initState() {
    super.initState();
    widget.control.addInvokeMethodListener(_invokeMethod);
    final source = PicturePasteSource.shared;
    if (!source.supported) return;
    _unregister = source.dispatcher.register(
      PictureRegion(
        ownerId: _owner,
        eligible: () => mounted && _focus.hasFocus,
        onOffer: (requestId, mime, size) {
          _offered.add(requestId);
          // The binding is the activation revision Python published for the
          // facts present now; it is read at the gesture and echoed back.
          widget.control.triggerEvent('offer', {
            'request_id': requestId,
            'mime': mime,
            'size': size,
            'binding': widget.control.getString('binding', '') ?? '',
          });
        },
        onReject: (reason) =>
            widget.control.triggerEvent('reject', {'reason': reason}),
        // The terminal's paste: Python classifies it (single line inserts,
        // several lines or controls go to review) and never logs it.
        // Text carries the gesture's binding too: a late event never reaches a new writer.
        onText: (text) => widget.control.triggerEvent('text', {
          'text': text,
          'binding': widget.control.getString('binding', '') ?? '',
        }),
        picturesEnabled: () => widget.control.getBool('pictures', true)!,
      ),
    );
  }

  @override
  void dispose() {
    widget.control.removeInvokeMethodListener(_invokeMethod);
    _unregister?.call();
    _unregister = null;
    PicturePasteSource.shared.abortOwner(_owner);
    _offered.clear();
    _focus.dispose();
    super.dispose();
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    final source = PicturePasteSource.shared;
    switch (name) {
      case 'upload':
        final requestId = '${args['request_id']}';
        if (!_offered.contains(requestId)) return 'gone';
        final result = await source.upload(
          requestId,
          '${args['upload_url']}',
          (args['expires_ms'] as num?)?.toInt() ?? 120000,
          _owner,
        );
        _offered.remove(requestId);
        return result;
      case 'abort':
        final requestId = '${args['request_id']}';
        _offered.remove(requestId);
        source.abort(requestId, _owner);
        return null;
      default:
        throw Exception('Unknown RelayDeckPicturePaste method: $name');
    }
  }

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    // Fields below this region (Composer, direct typing) handle Cmd/Ctrl+V as a
    // shortcut that would suppress the browser's paste event; leave it to the
    // browser while focus is inside so a picture can be seen.
    if (kIsWeb &&
        event is KeyDownEvent &&
        event.logicalKey == LogicalKeyboardKey.keyV &&
        (HardwareKeyboard.instance.isMetaPressed ||
            HardwareKeyboard.instance.isControlPressed) &&
        !HardwareKeyboard.instance.isShiftPressed &&
        !HardwareKeyboard.instance.isAltPressed &&
        PicturePasteSource.shared.dispatcher.shortcutHandoff) {
      return KeyEventResult.skipRemainingHandlers;
    }
    return KeyEventResult.ignored;
  }

  @override
  Widget build(BuildContext context) {
    final content = widget.control.buildWidget('content') ?? const SizedBox();
    return LayoutControl(
      control: widget.control,
      child: Focus(
        focusNode: _focus,
        onKeyEvent: _onKey,
        child: content,
      ),
    );
  }
}
