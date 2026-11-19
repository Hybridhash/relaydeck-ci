import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart' show defaultTargetPlatform, kIsWeb;
import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter/rendering.dart' show ScrollDirection;
import 'package:flutter/scheduler.dart';
import 'package:flutter/widgets.dart';
import 'package:xterm2/xterm.dart';

import 'ack.dart';
import 'picture_paste.dart';
import 'snapshot.dart';
import 'swipe_math.dart';
import 'utf8_accumulator.dart';
import 'visual_viewport.dart';
import 'wheel_queue.dart';

// Packet kinds shared with relaydeck_terminal/protocol.py.
const _kWrite = 0x01;
const _kInputEnabled = 0x03;
const _kRequestSelectionText = 0x06;
const _kClearSelection = 0x07;
const _kReset = 0x08;
const _kRequestSnapshot = 0x09;
const _kRequestKeyboard = 0x0A;
const _kScrollViewport = 0x0B;
const _kResumeProbe = 0x0C;
// Sent by Python only after a resume positively completes on this
// binding: releases the transport-loss input fence.
const _kResumeVerified = 0x0D;
// Hides the soft keyboard and disarms touch input (Python
// release_keyboard). Never writes to the PTY.
const _kReleaseKeyboard = 0x0E;
// 0x0F shows the native keyboard button (Python sends);
// 0x88 reports the armed state back to Python (viewer sends, never the PTY).
const _kKeyboardButton = 0x0F;
const _kKeyboardState = 0x88;
const _kKeyboardTrace = 0x89;
// 0x10 + u32be seq retires widget scroll motion before a server-side
// key/paste write (Python sends); 0x8A + u32be seq acks it back
// (viewer sends, never the PTY; seq 0 at mount proves the capability).
const _kCancelScroll = 0x10;
const _kScrollCancelled = 0x8A;
const _kPageMetrics = 0x8B;

const _kInput = 0x81;
const _kSizeReport = 0x82;
const _kSelectionText = 0x83;
const _kSnapshotChunk = 0x84;
const _kModeReport = 0x85;
const _kViewReport = 0x86;
const _kResumeReport = 0x87;
// _kAck (0xFF) lives in ack.dart as kAckKind (dart2js-safe encoding).

// Snapshot payload bytes per chunk: 12 header bytes + payload stays
// well under the 64 KiB protocol packet bound.
const _kSnapshotChunkSize = 32 * 1024;

const _kOriginText = 1;
const _kOriginKey = 2;
const _kOriginMouse = 3;
// Alt-buffer swipe with no mouse reporting: direction + line count
// (protocol.py ORIGIN_SWIPE). Python translates to bounded pages;
// these bytes never reach the PTY raw and are never arrows.
const _kOriginSwipe = 4;
const _kSwipeUp = 0;
const _kSwipeDown = 1;

/// xterm2 [Terminal] that tags the origin of [onOutput] bytes so Python
/// policy can tell committed text, encoded keys, and mouse reports apart.
class _TaggedTerminal extends Terminal {
  _TaggedTerminal({required super.maxLines});

  /// Observe actual mode and buffer mutations synchronously so batched notifications cannot
  /// hide a round trip. No-op assignments leave the gesture generation intact.
  VoidCallback? onSwipeBoundary;

  int _pendingOrigin = _kOriginText;

  @override
  void setMouseMode(MouseMode mode) {
    final changed = mode.reportScroll != mouseMode.reportScroll;
    super.setMouseMode(mode);
    if (changed) onSwipeBoundary?.call();
  }

  @override
  void useAltBuffer() {
    final changed = !isUsingAltBuffer;
    super.useAltBuffer();
    if (changed) onSwipeBoundary?.call();
  }

  @override
  void useMainBuffer() {
    final changed = isUsingAltBuffer;
    super.useMainBuffer();
    if (changed) onSwipeBoundary?.call();
  }

  int takeOrigin() {
    final origin = _pendingOrigin;
    _pendingOrigin = _kOriginText;
    return origin;
  }

  @override
  void textInput(String text) {
    _pendingOrigin = _kOriginText;
    super.textInput(text);
  }

  @override
  bool keyInput(
    TerminalKey key, {
    bool shift = false,
    bool alt = false,
    bool ctrl = false,
    bool superKey = false,
    bool capsLock = false,
    bool numLock = false,
    TerminalKeyEventType type = TerminalKeyEventType.press,
    String? text,
  }) {
    _pendingOrigin = _kOriginKey;
    return super.keyInput(
      key,
      shift: shift,
      alt: alt,
      ctrl: ctrl,
      superKey: superKey,
      capsLock: capsLock,
      numLock: numLock,
      type: type,
      text: text,
    );
  }

  @override
  bool mouseInput(
    TerminalMouseButton button,
    TerminalMouseButtonState buttonState,
    CellOffset position, {
    bool motion = false,
    TerminalMouseModifiers modifiers = TerminalMouseModifiers.none,
    CellOffset? pixelPosition,
  }) {
    _pendingOrigin = _kOriginMouse;
    return super.mouseInput(
      button,
      buttonState,
      position,
      motion: motion,
      modifiers: modifiers,
      pixelPosition: pixelPosition,
    );
  }
}

class RelayDeckTerminalControl extends StatefulWidget {
  final Control control;

  const RelayDeckTerminalControl({super.key, required this.control});

  @override
  State<RelayDeckTerminalControl> createState() =>
      _RelayDeckTerminalControlState();
}

class _RelayDeckTerminalControlState extends State<RelayDeckTerminalControl>
    with SingleTickerProviderStateMixin {
  late final _TaggedTerminal _terminal;
  late final TerminalController _controller;
  // Owned scroll controller for the TerminalView (its state keeps the
  // controller private): SCROLL_VIEWPORT jumps and at-bottom reads go
  // through here, never through the emulator or the PTY.
  late final ScrollController _scrollController;
  int? _lastViewBits;
  bool _viewReportQueued = false;
  // First active pointer driving the local drag. xterm's application
  // scroller wins the gesture arena while mouse reporting is on, so
  // normal-buffer drags are tracked with arena-independent Listener
  // events instead; extra fingers are ignored.
  int? _localDragPointer;
  // Release-velocity tracker for mode-B momentum, fed only while a local
  // drag runs; after pointer-up the ticker coasts the owned view until
  // friction, an edge or a boundary stops it.
  VelocityTracker? _flingTracker;
  Ticker? _flingTicker;
  double _flingV = 0;
  Duration? _flingLast;
  // Set by a scroll retire (cancel or boundary): mode-C wheel reports
  // drop until the next fresh gesture (pointer-down or wheel event),
  // so inner-ballistic leftovers can never produce rows after a retiral.
  bool _wheelCutoff = false;
  // First active pointer driving an alt/no-mouse swipe. Raw
  // depth pixels accumulate per direction run and emit page packets
  // live plus a remainder flush on release; wheel-only input flushes
  // after a short idle instead (trackpads/wheels have no release).
  int? _swipePointer;
  double _swipePixels = 0;
  Timer? _swipeIdle;
  // Advance the boundary generation when input, reset or mode transitions retire gestures.
  // Delayed callbacks must match their arming generation even if eligibility has since
  // returned.
  int _swipeEpoch = 0;
  // Last observed (alt, reportScroll) bits; -1 until the first terminal
  // notification. Compared synchronously on every notification so even
  // a quick toggle retires immediately.
  int _swipeFenceBits = -1;
  // Same TerminalView for the control's lifetime; the key only lets a
  // REQUEST_KEYBOARD packet reach its text-input focus.
  final _viewKey = GlobalKey<TerminalViewState>();
  // On touch devices the text input stays mounted (readOnly is
  // unchanged) but cannot take focus until the keyboard button arms it, so taps
  // and scrolls never open the keyboard. REQUEST_KEYBOARD arms; release or focus
  // loss disarms.
  bool _keyboardArmed = false;
  bool _buttonDownShown = false;
  bool _buttonGestureActive = false;
  final _keyboardTraceSamples = <Timer>{};
  void Function()? _unwatchPage;
  // Native keyboard button: shown only when Python enables it.
  bool _keyboardButton = false;
  final _focusNode = FocusNode();
  bool get _touch =>
      defaultTargetPlatform == TargetPlatform.iOS ||
      defaultTargetPlatform == TargetPlatform.android;
  DataChannel? _channel;
  StreamSubscription<Uint8List>? _sub;
  final BytesBuilder _inbox = BytesBuilder(copy: false);
  int _appliedBytes = 0;
  // Native identity: minted once per engine mount, stable across
  // socket reconnects, fresh on every remount. Under 2^53 so dart2js
  // doubles encode it exactly (same bound as ACKs in ack.dart).
  late final int _nativeInstanceId;
  // Applied RESET count; Python compares it with its issued count so
  // a reset lost in an outage fails the resume closed instead of
  // validating offsets against mismatched bases.
  int _dartResetCount = 0;
  // Input fence: latched once the transport has ever been live, then
  // fencing while Flet reports (re)connecting. Boot-time input keeps
  // flowing (Python gates on input_enabled); only a live channel that
  // drops fences, so offline gestures are dropped, never queued.
  bool _transportWasLive = false;
  bool _transportFenced = false;
  FletBackend? _backend;
  int _lastWidth = 80;
  int _lastHeight = 24;
  // Incremental UTF-8 decode holding split multibyte sequences across
  // DataChannel deliveries. Escape sequences pass through to
  // the emulator, which owns their parse state.
  final _utf8Sink = Utf8Accumulator();
  // Paced alternate-buffer wheel runs (agent TUIs): bounded rows released
  // three per tick instead of one round trip per line.
  late final WheelQueue _wheel;

  @override
  void initState() {
    super.initState();
    _focusNode.addListener(_onFocusChanged);
    if (_touch) _unwatchPage = watchPage(_onPageChanged);
    _applyFocusGate();
    final random = Random();
    _nativeInstanceId =
        random.nextInt(0x100000000) * 0x100000 + random.nextInt(0x100000);
    _terminal = _TaggedTerminal(
      maxLines: widget.control.getInt('max_lines', 5000)!,
    );
    // Security: leave onClipboardStore/onOpenUrl/onNotification unset so
    // OSC 52 writes, automatic URL opening, and OSC 9/777 notifications
    // from untrusted terminal output are all denied.
    _terminal.onOutput = _onTerminalOutput;
    _terminal.onResize = _onTerminalResize;
    _wheel = WheelQueue(send: (report) {
      // A fence raised mid-drain retires the run instead of sending it.
      if (_transportFenced || !_inputEnabled) {
        _wheel.discard();
        return;
      }
      _sendInput(_kOriginMouse, utf8.encode(report));
    });
    _controller = TerminalController();
    _scrollController = ScrollController();
    _scrollController.addListener(_onScrollChanged);
    // Mode/buffer changes (alt screen, mouse reporting) arrive as
    // terminal notifications; scroll position changes via the
    // controller listener above. Both funnel into one debounced
    // per-frame VIEW_REPORT.
    _terminal.addListener(_scheduleViewReport);
    _terminal.addListener(_swipeWatchMode);
    // Retire swipes at actual mode mutations independently of repaint notifications. Retain
    // notification checks for reset paths that assign engine fields directly.
    _terminal.onSwipeBoundary = _gestureBoundary;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _scheduleViewReport();
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_sub != null) return;
    final backend = FletBackend.of(context);
    final channel = backend.openDataChannel();
    _channel = channel;
    _sub = channel.messages.listen(_onPythonMessage);
    widget.control.triggerEvent('data_channel_open', {
      'channel_name': 'terminal',
      'channel_id': channel.id,
    });
    // Mount hello (req 0): records this engine's identity on Python
    // before any output flows. Same-socket FIFO keeps it behind the
    // open event, so Python captures the channel first.
    _sendResumeReport(0);
    // Capability advertisement: this viewer answers CANCEL_SCROLL, so
    // Python may block wheel input around server-side writes. Older
    // bundles never send this and keep the unblocked behavior.
    _sendScrollCancelled(0);
    _backend = backend;
    _onBackendChanged();
    backend.addListener(_onBackendChanged);
  }

  /// Latch the input fence and retire gestures when a live transport starts reconnecting.
  /// Re-registration alone cannot release it; only verified continuity or explicit reset can.
  void _onBackendChanged() {
    final backend = _backend;
    if (backend == null) return;
    if (!backend.isLoading) {
      _transportWasLive = true;
    } else if (_transportWasLive) {
      _transportFenced = true;
      _localDragPointer = null;
      _gestureBoundary();
    }
  }

  @override
  void didUpdateWidget(covariant RelayDeckTerminalControl oldWidget) {
    super.didUpdateWidget(oldWidget);
    // input_enabled prop changes also arrive as channel packets; the prop
    // is the source of truth for rebuilds (e.g. after remount).
    setState(() {});
  }

  @override
  void dispose() {
    _unwatchPage?.call();
    for (final timer in _keyboardTraceSamples) {
      timer.cancel();
    }
    _keyboardTraceSamples.clear();
    _focusNode.removeListener(_onFocusChanged);
    final paste = PicturePasteSource.shared.dispatcher;
    if (paste.terminalFocused == _hasFocus) paste.terminalFocused = null;
    _focusNode.dispose();
    _terminal.removeListener(_scheduleViewReport);
    _terminal.removeListener(_swipeWatchMode);
    _terminal.onSwipeBoundary = null;
    _backend?.removeListener(_onBackendChanged);
    _backend = null;
    _swipeIdle?.cancel();
    _swipeIdle = null;
    _wheel.discard();
    _flingTicker?.stop();
    _flingTicker?.dispose();
    _flingTicker = null;
    _scrollController.dispose();
    _sub?.cancel();
    _channel?.close();
    super.dispose();
  }

  // Losing text-input focus (keyboard dismissed) disarms
  // touch input, so the next tap or scroll cannot reopen the keyboard.
  void _onFocusChanged() {
    _sendKeyboardTrace(_focusNode.hasFocus ? 4 : 5);
    final paste = PicturePasteSource.shared.dispatcher;
    if (_focusNode.hasFocus) {
      paste.terminalFocused = _hasFocus;
    } else if (paste.terminalFocused == _hasFocus) {
      paste.terminalFocused = null;
    }
    if (!_focusNode.hasFocus &&
        _keyboardArmed &&
        !_buttonGestureActive &&
        mounted) {
      _disarmKeyboard();
    }
  }

  /// Touch focus is allowed only while armed; desktop always may focus.
  void _applyFocusGate() {
    _focusNode.canRequestFocus = !_touch || _keyboardArmed;
  }

  void _disarmKeyboard() {
    final wasArmed = _keyboardArmed;
    _keyboardArmed = false;
    _applyFocusGate();
    if (wasArmed) _sendKeyboardState(false);
    if (mounted) setState(() {});
  }

  void _sendKeyboardState(bool shown) {
    _channel?.send(Uint8List.fromList([_kKeyboardState, shown ? 1 : 0]));
  }

  void _sendKeyboardTrace(int event) {
    if (!_touch || !mounted) return;
    final height = visualViewportHeight();
    final viewport = height != null && height.isFinite && height >= 0
        ? height.round().clamp(0, 0xFFFE).toInt()
        : 0xFFFF;
    final flags = (_keyboardArmed ? 1 : 0) |
        (_focusNode.hasFocus ? 2 : 0) |
        (_viewKey.currentState?.hasInputConnection == true ? 4 : 0) |
        (_inputEnabled ? 8 : 0) |
        (_buttonDownShown ? 16 : 0);
    final packet = Uint8List.fromList([_kKeyboardTrace, event, flags, 0, 0]);
    ByteData.sublistView(packet).setUint16(3, viewport, Endian.big);
    _channel?.send(packet);
  }

  void _onPageChanged() {
    if (!mounted) return;
    // Every visual viewport change reaches Python: the keyboard's stages, the accessory
    // bar, and the dismissal that restores the full height (a zero inset).
    final before = pageMetrics();
    _sendPageMetrics(pinPageTop(), before);
  }

  void _sendPageMetrics(bool reset, List<double>? metrics) {
    if (!_touch || !mounted || metrics == null) return;
    int signed(double v) =>
        v.isFinite ? v.round().clamp(-32768, 32767).toInt() : 0;
    int size(double v) =>
        v.isFinite ? v.round().clamp(0, 0xFFFE).toInt() : 0xFFFF;
    final packet = Uint8List(10);
    ByteData.sublistView(packet)
      ..setUint8(0, _kPageMetrics)
      ..setUint8(1, reset ? 1 : 0)
      ..setInt16(2, signed(metrics[0]))
      ..setInt16(4, signed(metrics[1]))
      ..setUint16(6, size(metrics[2]))
      ..setUint16(8, size(metrics[3]));
    _channel?.send(packet);
  }

  void _scheduleKeyboardTraceSample() {
    if (!_touch) return;
    late final Timer timer;
    timer = Timer(const Duration(milliseconds: 400), () {
      _keyboardTraceSamples.remove(timer);
      if (mounted) {
        _sendKeyboardTrace(9);
        _sendPageMetrics(false, pageMetrics());
      }
    });
    _keyboardTraceSamples.add(timer);
  }

  void _onKeyboardButtonDown(PointerDownEvent event) {
    _buttonDownShown = _keyboardArmed;
    _buttonGestureActive = true;
    _sendKeyboardTrace(1);
  }

  void _onKeyboardButtonCancel() {
    _buttonGestureActive = false;
    if (!_focusNode.hasFocus && _keyboardArmed) _disarmKeyboard();
    _sendKeyboardTrace(3);
  }

  /// The native button's tap. The focus happens synchronously inside the tap,
  /// because iOS opens the soft keyboard only from the user's gesture.
  void _onKeyboardButtonTap() {
    final wasShown = _buttonGestureActive ? _buttonDownShown : _keyboardArmed;
    _buttonGestureActive = false;
    if (!_inputEnabled) {
      _sendKeyboardTrace(2);
      _scheduleKeyboardTraceSample();
      return;
    }
    if (!wasShown) {
      _keyboardArmed = true;
      _applyFocusGate();
      _viewKey.currentState?.requestKeyboard();
      _sendKeyboardState(true);
    } else {
      _releaseKeyboard();
    }
    _sendKeyboardTrace(2);
    _scheduleKeyboardTraceSample();
    if (mounted) setState(() {});
  }

  Widget _keyboardButtonWidget() {
    final shown = _keyboardArmed;
    return Semantics(
      label: shown ? 'Hide keyboard' : 'Show keyboard',
      button: true,
      child: Listener(
        onPointerDown: _onKeyboardButtonDown,
        child: GestureDetector(
          key: const ValueKey('keyboard-button'),
          behavior: HitTestBehavior.opaque,
          onTap: _onKeyboardButtonTap,
          onTapCancel: _onKeyboardButtonCancel,
          child: Container(
            width: 32,
            height: 32,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              color: Color(0x59343434),
            ),
            child: Icon(
              shown ? Icons.keyboard_hide : Icons.keyboard,
              size: 20,
              color: const Color(0xFFFFFFFF),
            ),
          ),
        ),
      ),
    );
  }

  void _releaseKeyboard() {
    _viewKey.currentState?.closeKeyboard();
    _focusNode.unfocus();
    _disarmKeyboard();
  }

  bool? _inputEnabledOverride;

  bool get _inputEnabled =>
      _inputEnabledOverride ?? widget.control.getBool('input_enabled', false)!;

  void _onTerminalOutput(String data) {
    // Drop engine output while display-only because readOnly does not block every hardware-key
    // path. Selection remains available without changing display state.
    if (!_inputEnabled) return;
    final origin = _terminal.takeOrigin();
    var bytes = utf8.encode(data);
    if (origin == _kOriginMouse && _localGestureMode) {
      // Drive normal-buffer mouse-mode scrolling from raw pointer deltas beyond protocol
      // coordinate limits. Consume vertical wheel reports to prevent PTY delivery or duplicate
      // scrolling.
      final wheels = extractWheelScroll(data);
      if (wheels.total > 0) {
        if (wheels.remainder.isEmpty) return;
        bytes = utf8.encode(wheels.remainder);
      }
    }
    if (origin == _kOriginMouse &&
        _terminal.isUsingAltBuffer &&
        _terminal.mouseMode.reportScroll) {
      // Alternate buffer with mouse reporting on (agent TUIs): xterm2 runs
      // the fling locally and emits one wheel report per line crossed.
      // Those lines pace through the bounded queue instead of one round
      // trip each; clicks, motion and text still forward immediately.
      final wheels = extractWheelScroll(data);
      if (wheels.total > 0) {
        if (_transportFenced || !_inputEnabled || _wheelCutoff) {
          // Fenced or disabled mid-gesture, or past a scroll retire:
          // wheel rows are stale the moment they arrive, so they
          // never queue; anything held is dropped too, and can never
          // replay after recovery or past the cancel boundary.
          _wheel.discard();
          if (wheels.remainder.isEmpty) return;
          bytes = utf8.encode(wheels.remainder);
        } else {
          for (final run in _wheelRuns(data)) {
            _wheel.enqueue(run.towardOlder, run.rows, run.report);
          }
          if (wheels.remainder.isEmpty) return;
          bytes = utf8.encode(wheels.remainder);
        }
      }
    }
    _sendInput(origin, bytes);
  }

  /// Use raw-pointer local scrolling for normal-buffer mouse mode in both control and watch
  /// views. Alternate-buffer wheel forwarding and readOnly handling stay with the emulator.
  bool get _localGestureMode =>
      !_terminal.isUsingAltBuffer && _terminal.mouseMode.reportScroll;

  /// Pixel scroll of the owned local view; clamps, never the PTY.
  void _scrollLocalPixels(double dy) {
    if (!mounted || !_scrollController.hasClients || dy == 0) return;
    final position = _scrollController.position;
    final target = (position.pixels + dy).clamp(0.0, position.maxScrollExtent);
    if (target != position.pixels) position.jumpTo(target);
    _followTail = _atBottom;
    _scheduleViewReport();
  }

  /// Halts the momentum coast where it stands: pointer-down, boundary,
  /// edge, friction floor. Stopping an idle ticker is harmless.
  void _flingStop() {
    _flingTicker?.stop();
    _flingLast = null;
    _flingV = 0;
  }

  /// Coasts the owned view at [v] logical px/s (already clamped), same
  /// sign convention as the drag: finger-up velocity scrolls on.
  void _flingStart(double v) {
    _flingStop();
    _flingV = v;
    _flingTicker ??= createTicker(_flingTick);
    _flingTicker!.start();
  }

  void _flingTick(Duration elapsed) {
    final last = _flingLast;
    _flingLast = elapsed;
    if (!mounted || last == null) return;
    var dt = (elapsed - last).inMicroseconds / 1e6;
    if (dt <= 0) return;
    if (dt > 1 / 30) dt = 1 / 30;
    if (_flingV.abs() < 20 || !_scrollController.hasClients) {
      _flingStop();
      return;
    }
    final before = _scrollController.position.pixels;
    _scrollLocalPixels(-_flingV * dt);
    if (_scrollController.position.pixels == before) {
      // Clamped at an edge: nothing left to coast into.
      _flingStop();
      return;
    }
    _flingV *= pow(0.998, dt * 1000).toDouble();
  }

  /// Alternate screen without mouse scroll reporting needs explicit swipe
  /// packets so xterm2's arrow fallback cannot send Up/Down into the app. Mouse
  /// reporting and the normal buffer are untouched.
  bool get _swipeMode =>
      _terminal.isUsingAltBuffer && !_terminal.mouseMode.reportScroll;

  /// Current cell line height, the same metric xterm2 quantizes its own
  /// scroll gestures with. Null before layout (nothing can gesture yet).
  double? _swipeLineHeight() {
    if (!mounted) return null;
    try {
      final height = _viewKey.currentState?.renderTerminal.lineHeight;
      return (height == null || height <= 0) ? null : height;
    } catch (_) {
      return null;
    }
  }

  /// Synchronous mode fence: alt/mouse transitions retire pending
  /// swipe state immediately, so a quick toggle can never resurrect a
  /// stale run or timer.
  void _swipeWatchMode() {
    final bits = (_terminal.isUsingAltBuffer ? 1 : 0) |
        (_terminal.mouseMode.reportScroll ? 2 : 0);
    if (_swipeFenceBits != -1 && bits != _swipeFenceBits) {
      _gestureBoundary();
    }
    _swipeFenceBits = bits;
  }

  /// Retire swipe pixels, timer and pointer generation so a fresh press must start the next
  /// run. Wheel queues and momentum have separate boundary retirement.
  void _swipeRetire() {
    _swipeEpoch++;
    _swipePointer = null;
    _swipePixels = 0;
    _swipeIdle?.cancel();
    _swipeIdle = null;
  }

  /// Retire swipe, wheel, coast and release velocity at a mode, transport or reset boundary.
  void _gestureBoundary() {
    _swipeRetire();
    _retireScrollMotion();
  }

  /// Scroll motion without the swipe run: stops any coast, forgets the
  /// pending release velocity and drops queued wheel rows. Explicit
  /// viewport navigation calls this before moving, so nothing scrolls
  /// the view back afterwards.
  void _retireScrollMotion() {
    _flingStop();
    _localDragPointer = null;
    _flingTracker = null;
    _wheel.discard();
    // xterm2's inner ballistic outlives this retire; its rows must
    // never become fresh scroll after a cancel or boundary.
    _wheelCutoff = true;
  }

  /// Drag depth in scroll-depth pixels (+down toward latest). Gated on
  /// swipe mode and enabled input like every PTY-bound path; anything
  /// else retires the run so stale runs never fire into a new mode.
  void _swipeMove(int pointer, double depth) {
    if (!_swipeMode || !_inputEnabled) {
      _swipeRetire();
      return;
    }
    if (pointer != _swipePointer) return;
    final height = _swipeLineHeight();
    if (height == null) return;
    _swipeAccumulate(depth, height, idleFlush: false);
  }

  /// Wheel/trackpad depth; same gates and accumulator as drags, with an
  /// idle flush because wheel input has no release event.
  void _swipeWheel(double depth) {
    if (!_swipeMode || !_inputEnabled) {
      _swipeRetire();
      return;
    }
    final height = _swipeLineHeight();
    if (height == null) return;
    _swipeAccumulate(depth, height, idleFlush: true);
  }

  /// Release (or cancel) of the swipe pointer: flush a real remainder,
  /// then forget the pointer.
  void _swipeRelease(int pointer) {
    // The slot is the release fence: any retirement cleared it, so only
    // the still-armed pointer of this generation can flush.
    if (pointer != _swipePointer) return;
    _swipePointer = null;
    if (!_swipeMode || !_inputEnabled) {
      _swipeRetire();
      return;
    }
    final height = _swipeLineHeight();
    if (height == null) {
      _swipeRetire();
      return;
    }
    _swipeFlush(height);
  }

  /// Adds one run of depth pixels; emits a page packet per viewport
  /// crossed live. A direction flip commits the current run first so
  /// one packet never mixes directions.
  void _swipeAccumulate(
    double depth,
    double lineHeight, {
    required bool idleFlush,
  }) {
    if (depth == 0) return;
    if (_swipePixels != 0 && depth.sign != _swipePixels.sign) {
      _swipeFlush(lineHeight);
    }
    _swipePixels += depth;
    final rows = _lastHeight > 0 ? _lastHeight : 24;
    final page = lineHeight * rows;
    while (_swipePixels.abs() >= page) {
      _sendSwipe(_swipePixels > 0 ? _kSwipeDown : _kSwipeUp, rows);
      _swipePixels -= _swipePixels.sign * page;
    }
    if (idleFlush) {
      _swipeIdle?.cancel();
      // Fenced to the arming generation: a boundary crossed during the
      // idle window (disable-enable, mode round trip, reset) retires,
      // even if current eligibility looks restored.
      final epoch = _swipeEpoch;
      _swipeIdle = Timer(const Duration(milliseconds: 150), () {
        _swipeIdle = null;
        if (!mounted || epoch != _swipeEpoch || !_swipeMode || !_inputEnabled) {
          _swipeRetire();
          return;
        }
        final height = _swipeLineHeight();
        if (height != null) {
          // Slow wheel/trackpad runs span idle windows; keep the sub-line
          // remainder so they accumulate instead of vanishing.
          _swipeFlush(height, keepRemainder: true);
        } else {
          _swipePixels = 0;
        }
      });
    }
  }

  /// Emits the accumulated run, if it reaches a full line. Sub-line
  /// remainders (micro-touches) emit nothing; with [keepRemainder] a wheel's
  /// idle flush leaves them in the run so slow scrolling still crosses a line.
  void _swipeFlush(double lineHeight, {bool keepRemainder = false}) {
    _swipeIdle?.cancel();
    _swipeIdle = null;
    final pixels = _swipePixels;
    if (pixels == 0) return;
    final flush =
        swipeFlushAccount(pixels, lineHeight, keepRemainder: keepRemainder);
    _swipePixels = flush.remainder;
    if (flush.lines < 1) return;
    _sendSwipe(pixels > 0 ? _kSwipeDown : _kSwipeUp, flush.lines);
  }

  void _sendSwipe(int direction, int lines) {
    _sendInput(
      _kOriginSwipe,
      Uint8List.fromList([direction, lines.clamp(1, 255)]),
    );
  }

  KeyEventResult _onKeyEvent(FocusNode node, KeyEvent event) {
    // Intercept Shift+Enter as LF to match the application key encoder. The physical Enter key
    // uses the CR keytab rule rather than the separate Return-key rule.
    if (_inputEnabled &&
        event is KeyDownEvent &&
        event.logicalKey == LogicalKeyboardKey.enter &&
        HardwareKeyboard.instance.isShiftPressed) {
      _sendInput(_kOriginKey, Uint8List.fromList([0x0A]));
      return KeyEventResult.handled;
    }
    // The clipboard reaches the page only as a browser paste event, which a
    // handled Cmd/Ctrl+V would suppress. While a paste region owns focus the
    // shortcut is left to the browser; the region then offers a picture, or sends
    // text to Python's reviewed paste exactly once (never xterm's raw paste).
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

  bool _hasFocus() => mounted && _focusNode.hasFocus && _inputEnabled;

  void _sendInput(int origin, Uint8List bytes) {
    // Fresh non-mouse input obsoletes any queued wheel run: keystrokes
    // never wait behind scroll.
    if (origin != _kOriginMouse) _wheel.discard();
    // Reconnecting after a live session: drop user input quietly.
    // Flet queues no raw channel bytes, so whatever reaches the dead
    // socket is lost either way; the fence keeps the loss explicit
    // and local instead of gambling on sink behavior.
    if (_transportFenced) return;
    final packet = BytesBuilder(copy: false)
      ..addByte(_kInput)
      ..addByte(origin)
      ..add(_u32(bytes.length))
      ..add(bytes);
    _channel?.send(packet.toBytes());
  }

  void _onTerminalResize(int width, int height, int pxW, int pxH) {
    if (width == _lastWidth && height == _lastHeight) return;
    _lastWidth = width;
    _lastHeight = height;
    // A new geometry (keyboard, rotation) must not strand a reader who was at the tail.
    _followTailAfterLayout();
    _sendSizeReport();
  }

  // Track tail following from reader gestures and jump-to-latest actions. While enabled, repin
  // after output and geometry changes so layout notifications cannot strand the view.
  bool _followTail = true;

  void _onScrollChanged() {
    if (_scrollController.hasClients) {
      final position = _scrollController.position;
      // A drag or fling by the reader decides whether they follow the tail; layout-driven
      // notifications (idle direction) never do.
      if (position.userScrollDirection != ScrollDirection.idle) {
        _followTail = _atBottom;
      }
    }
    _scheduleViewReport();
  }

  void _followTailAfterLayout() {
    if (!_followTail || _terminal.isUsingAltBuffer || !mounted) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_followTail || !_scrollController.hasClients) return;
      final position = _scrollController.position;
      if (position.maxScrollExtent - position.pixels >= 1.0) {
        position.jumpTo(position.maxScrollExtent);
      }
    });
  }

  void _sendSizeReport() {
    final packet = BytesBuilder(copy: false)
      ..addByte(_kSizeReport)
      ..add(_u16(_lastWidth))
      ..add(_u16(_lastHeight));
    _channel?.send(packet.toBytes());
  }

  void _onPythonMessage(Uint8List bytes) {
    // Surface output-handler failures instead of silently dropping bytes. Stalled
    // acknowledgements force visible backpressure recovery without claiming undisplayed output.
    _inbox.add(bytes);
    final buf = _inbox.toBytes();
    var offset = 0;
    while (offset < buf.length) {
      final kind = buf[offset];
      int? consumed = _handlePacket(kind, buf, offset);
      if (consumed == null) break; // partial tail: wait for more bytes
      offset += consumed;
    }
    _inbox.clear();
    if (offset < buf.length) _inbox.add(buf.sublist(offset));
  }

  /// Returns bytes consumed, or null when the packet is incomplete.
  int? _handlePacket(int kind, Uint8List buf, int offset) {
    switch (kind) {
      case _kWrite:
        if (buf.length - offset < 5) return null;
        final len = _readU32(buf, offset + 1);
        if (buf.length - offset < 5 + len) return null;
        final chunk = buf.sublist(offset + 5, offset + 5 + len);
        _terminal.write(_utf8Sink.add(chunk));
        _followTailAfterLayout();
        _appliedBytes += len;
        _sendAck();
        _sendModeReport();
        _scheduleViewReport();
        return 5 + len;
      case _kInputEnabled:
        if (buf.length - offset < 2) return null;
        final wasInputEnabled = _inputEnabled;
        // Runtime mode switch (the prop only sets the mount-time value;
        // the app holds one control instance, so no prop patch follows).
        // Disabling retires pending swipe state: a delayed flush or
        // release must not emit an old gesture after disable-enable.
        if (buf[offset + 1] != 0x01) _gestureBoundary();
        _inputEnabledOverride = buf[offset + 1] == 0x01;
        if (!_inputEnabled) _disarmKeyboard();
        if (!wasInputEnabled && _inputEnabled && !_transportFenced) {
          _sendSizeReport();
        }
        _sendKeyboardTrace(8);
        setState(() {});
        return 2;
      case _kRequestSelectionText:
        final selection = _controller.selectionFor(_terminal.buffer);
        final text =
            selection == null ? '' : _terminal.buffer.getText(selection, true);
        final bytes = utf8.encode(text);
        _channel?.send((BytesBuilder(copy: false)
              ..addByte(_kSelectionText)
              ..add(_u32(bytes.length))
              ..add(bytes))
            .toBytes());
        return 1;
      case _kClearSelection:
        _controller.clearSelection();
        return 1;
      case _kRequestKeyboard:
        // Arm focus and open the soft keyboard only after input is enabled. This command never
        // writes to the PTY.
        if (_inputEnabled) {
          if (!_keyboardArmed) {
            setState(() => _keyboardArmed = true);
            _sendKeyboardState(true);
          }
          _applyFocusGate();
          _viewKey.currentState?.requestKeyboard();
        }
        _sendKeyboardTrace(6);
        _scheduleKeyboardTraceSample();
        return 1;
      case _kReleaseKeyboard:
        _releaseKeyboard();
        _sendKeyboardTrace(7);
        _scheduleKeyboardTraceSample();
        return 1;
      case _kKeyboardButton:
        if (buf.length - offset < 2) return null;
        _keyboardButton = buf[offset + 1] == 0x01;
        setState(() {});
        return 2;
      case _kCancelScroll:
        if (buf.length - offset < 5) return null;
        final cancelSeq = _readU32(buf, offset + 1);
        // Server-side input is about to land: retire scroll motion first
        // (arming the ballistic cutoff), then ack the sequence so Python
        // knows later wheel reports are a fresh gesture.
        _retireScrollMotion();
        _sendScrollCancelled(cancelSeq);
        return 5;
      case _kReset:
        _utf8Sink.reset();
        _terminal.reset();
        _gestureBoundary();
        _appliedBytes = 0;
        _dartResetCount++;
        _inputEnabledOverride = null;
        // An explicit resync from Python: both sides restart from a
        // known base, so the transport fence releases with it.
        _transportFenced = false;
        _sendAck();
        _sendModeReport();
        _scheduleViewReport();
        _sendSizeReport();
        return 1;
      case _kResumeProbe:
        if (buf.length - offset < 5) return null;
        final reqId = _readU32(buf, offset + 1);
        // Pure report: no engine, selection, scroll, or input state
        // moves. Req 0 is the hello-only slot and never a probe.
        if (reqId != 0) _sendResumeReport(reqId);
        return 5;
      case _kResumeVerified:
        // Python positively completed a resume on this binding:
        // continuity is proven, input may flow again.
        _transportFenced = false;
        _sendSizeReport();
        return 1;
      case _kRequestSnapshot:
        if (buf.length - offset < 5) return null;
        _sendSnapshot(_readU32(buf, offset + 1));
        return 5;
      case _kScrollViewport:
        if (buf.length - offset < 2) return null;
        _retireScrollMotion();
        _scrollViewport(_readI8(buf, offset + 1));
        // A quiet terminal has no pending frame; without one the jump
        // and its view report would wait for the next output.
        WidgetsBinding.instance.scheduleFrame();
        return 2;
      default:
        // Consume unknown kind bytes without closing the viewer. Compatibility requires unknown
        // control kinds to have no body.
        return 1;
    }
  }

  void _sendSnapshot(int reqId) {
    final bytes = utf8.encode(snapshotText(_terminal));
    // Always send at least one chunk (even when empty) so the Python
    // waiter completes instead of timing out on a blank screen.
    if (bytes.isEmpty) {
      _channel?.send(_snapshotChunk(reqId, 0, 0, Uint8List(0)));
      return;
    }
    for (var at = 0; at < bytes.length; at += _kSnapshotChunkSize) {
      var end = at + _kSnapshotChunkSize;
      if (end > bytes.length) end = bytes.length;
      _channel?.send(_snapshotChunk(
          reqId, bytes.length, at, Uint8List.fromList(bytes.sublist(at, end))));
    }
  }

  Uint8List _snapshotChunk(int reqId, int total, int offset, Uint8List data) {
    return (BytesBuilder(copy: false)
          ..addByte(_kSnapshotChunk)
          ..add(_u32(12 + data.length))
          ..add(_u32(reqId))
          ..add(_u32(total))
          ..add(_u32(offset))
          ..add(data))
        .toBytes();
  }

  void _sendAck() {
    // Two-u32 encoding (ack.dart): ByteData.setUint64 throws on dart2js.
    _channel?.send(encodeAck(_appliedBytes));
  }

  /// Report request, native instance, reset count and applied bytes in a fixed 25-byte packet.
  /// Encode u64 halves and saturate applied bytes exactly as ACKs do.
  void _sendResumeReport(int reqId) {
    var applied = _appliedBytes;
    if (applied < 0) applied = 0;
    if (applied > kMaxAckBytes) applied = kMaxAckBytes;
    final data = ByteData(25)
      ..setUint8(0, _kResumeReport)
      ..setUint32(1, reqId, Endian.big)
      ..setUint32(5, _nativeInstanceId ~/ 0x100000000, Endian.big)
      ..setUint32(9, _nativeInstanceId % 0x100000000, Endian.big)
      ..setUint32(13, _dartResetCount, Endian.big)
      ..setUint32(17, applied ~/ 0x100000000, Endian.big)
      ..setUint32(21, applied % 0x100000000, Endian.big);
    _channel?.send(data.buffer.asUint8List());
  }

  /// Scroll the local viewport by signed page counts or jump to the bottom with 0x7F. Consume
  /// the command without scrolling in the alternate buffer and never write to the PTY.
  void _scrollViewport(int pages) {
    _retireScrollMotion();
    if (_terminal.isUsingAltBuffer) return;
    if (!_scrollController.hasClients) return;
    final position = _scrollController.position;
    final double target;
    if (pages == 0x7F) {
      target = position.maxScrollExtent;
    } else {
      target = (position.pixels + pages * position.viewportDimension)
          .clamp(0.0, position.maxScrollExtent);
    }
    if (target != position.pixels) position.jumpTo(target);
    _followTail = pages == 0x7F || _atBottom;
    _scheduleViewReport();
  }

  /// Debounced VIEW_REPORT (0x86): at most one send per frame, and only
  /// when the bitfield actually changed.
  void _scheduleViewReport() {
    if (_viewReportQueued || !mounted) return;
    _viewReportQueued = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _viewReportQueued = false;
      if (!mounted) return;
      final bits = _viewBits;
      if (bits == _lastViewBits) return;
      _lastViewBits = bits;
      _channel?.send((ByteData(2)
            ..setUint8(0, _kViewReport)
            ..setUint8(1, bits))
          .buffer
          .asUint8List());
    });
    WidgetsBinding.instance.scheduleFrame();
  }

  int get _viewBits {
    var bits = 0;
    if (_terminal.isUsingAltBuffer) bits |= 0x01;
    if (_atBottom) bits |= 0x02;
    if (_terminal.mouseMode != MouseMode.none) bits |= 0x04;
    return bits;
  }

  bool get _atBottom {
    if (!_scrollController.hasClients) return true;
    final position = _scrollController.position;
    return position.maxScrollExtent - position.pixels < 1.0;
  }

  /// SCROLL_CANCELLED (0x8A) with the cancel sequence it answers
  /// (0 = the mount capability advertisement).
  void _sendScrollCancelled(int seq) {
    _channel?.send((BytesBuilder(copy: false)
          ..addByte(_kScrollCancelled)
          ..add(_u32(seq)))
        .toBytes());
  }

  void _sendModeReport() {
    // Engine truth for paste framing: the mode xterm2 itself holds
    // after this write/reset (RIS, split sequences, and multi-param
    // lists included) — Python must not re-parse the output bytes.
    final data = ByteData(2)
      ..setUint8(0, _kModeReport)
      ..setUint8(1, _terminal.bracketedPasteMode ? 0x01 : 0x00);
    _channel?.send(data.buffer.asUint8List());
  }

  @override
  Widget build(BuildContext context) {
    final fontSize = widget.control.getDouble('font_size', 14.0)!;
    // Web (CanvasKit) cannot use OS fonts: an unset family falls back to a
    // proportional face while cells are sized as monospace, which spreads
    // glyphs apart. Paint with the bundled JetBrains Mono (registered by
    // the page as "JetBrains Mono", the design typography).
    final fontFamily =
        widget.control.getString('font_family', 'JetBrains Mono')!;
    // Keep raw pointer listeners mounted so normal-buffer mouse-mode gestures scroll both
    // control and watch views without remounting the engine. Consume emulator wheel reports and
    // ignore pointer scrolling in other modes.
    final view = Listener(
      onPointerDown: (event) {
        // A fresh press always kills momentum; a tap lands here too.
        _flingStop();
        _wheelCutoff = false;
        if (_localGestures && _localDragPointer == null) {
          _localDragPointer = event.pointer;
          _flingTracker = VelocityTracker.withKind(event.kind)
            ..addPosition(event.timeStamp, event.position);
        }
        if (_swipeMode && _inputEnabled && _swipePointer == null) {
          _swipePointer = event.pointer;
        }
      },
      onPointerPanZoomStart: (event) {
        // A fresh native-trackpad pan is a new gesture (its later
        // updates are not: a pan cancelled mid-stream stays blocked).
        _wheelCutoff = false;
      },
      onPointerMove: (event) {
        if (_localGestures && event.pointer == _localDragPointer) {
          // Content follows the finger: finger up reveals later lines.
          _flingTracker?.addPosition(event.timeStamp, event.position);
          _scrollLocalPixels(-event.delta.dy);
        }
        // Swipe depth: finger up scrolls toward the latest output.
        _swipeMove(event.pointer, -event.delta.dy);
      },
      onPointerUp: (event) {
        if (event.pointer == _localDragPointer) {
          _localDragPointer = null;
          // Release velocity coasts only in the mode that tracked it.
          final v = _localGestures
              ? _flingTracker?.getVelocityEstimate()?.pixelsPerSecond.dy ?? 0.0
              : 0.0;
          _flingTracker = null;
          final coast = v.clamp(-4000.0, 4000.0);
          if (coast.abs() >= 80) _flingStart(coast);
        }
        _swipeRelease(event.pointer);
      },
      onPointerCancel: (event) {
        if (event.pointer == _localDragPointer) {
          _localDragPointer = null;
          // Cancelled, not released: no coast.
          _flingTracker = null;
        }
        _swipeRelease(event.pointer);
      },
      onPointerSignal: (event) {
        if (event is PointerScrollEvent) _wheelCutoff = false;
        if (_localGestures && event is PointerScrollEvent) {
          _flingStop();
          _scrollLocalPixels(event.scrollDelta.dy);
        }
        // Wheel depth: wheel down scrolls toward the latest output.
        if (event is PointerScrollEvent) {
          _swipeWheel(event.scrollDelta.dy);
        }
      },
      child: TerminalView(
        _terminal,
        key: _viewKey,
        controller: _controller,
        scrollController: _scrollController,
        readOnly: !_inputEnabled,
        focusNode: _focusNode,
        onKeyEvent: _onKeyEvent,
        deleteDetection: true,
        // Disable xterm2's alt/no-mouse arrow fallback to prevent scrolling
        // from sending prompt history/selection keys. The bridge-owned
        // Listener above emits explicit swipe packets instead; mouse
        // reporting still flows through xterm untouched.
        simulateScroll: false,
        hardwareKeyboardOnly: false,
        textStyle: TerminalStyle(
          fontSize: fontSize,
          fontFamily: fontFamily,
          // Noto Sans Symbols 2 (page.fonts) covers the dingbats,
          // geometric shapes, misc symbols and braille agents emit
          // (U+2733 spinners/status) that JetBrains Mono lacks.
          fontFamilyFallback: const [
            'JetBrains Mono',
            'Noto Sans Symbols 2',
            'Noto Sans Symbols',
            'monospace',
          ],
        ),
        // onHyperlinkTap intentionally unset: untrusted output must not
        // open URLs without an explicit user action through Python.
      ),
    );
    return LayoutControl(
      control: widget.control,
      child: Stack(
        fit: StackFit.expand,
        children: [
          view,
          if (_keyboardButton && _inputEnabled)
            Positioned(right: 8, bottom: 8, child: _keyboardButtonWidget()),
        ],
      ),
    );
  }

  /// Listener driving applies in any mouse-reporting normal buffer:
  /// xterm owns the arena and its reports cannot be trusted past
  /// coordinate limits, so both control and watch scroll from raw
  /// pointer deltas while parsed wheel reports are only consumed.
  bool get _localGestures => _localGestureMode;
}

Uint8List _u16(int v) =>
    (ByteData(2)..setUint16(0, v, Endian.big)).buffer.asUint8List();
Uint8List _u32(int v) =>
    (ByteData(4)..setUint32(0, v, Endian.big)).buffer.asUint8List();
int _readU32(Uint8List b, int o) =>
    ByteData.sublistView(b, o, o + 4).getUint32(0, Endian.big);
int _readI8(Uint8List b, int o) => ByteData.sublistView(b, o, o + 1).getInt8(0);

/// Vertical wheel lines extracted from mouse-origin emulator output.
class WheelScroll {
  const WheelScroll(this.up, this.down, this.remainder);

  final int up;
  final int down;
  final String remainder;

  int get total => up + down;
}

// Recognize X10, SGR and urxvt mouse reports with modifier bits intact. Wheel IDs 64/65 encode
// vertical direction; coordinate limits and full layouts are documented in
// terminal-extension.md.
final _x10Wheel = RegExp('\x1b\\[M([\x20-\xFF])([\x20-\u07FF])([\x20-\u07FF])');
final _sgrWheel = RegExp('\x1b\\[<(\\d+);(\\d+);(\\d+)([Mm])');
final _urxvtWheel = RegExp('\x1b\\[(\\d+);(\\d+);(\\d+)M');

/// Extract vertical wheel counts and preserve all remaining mouse output for the forwarding
/// policy. Require wheel bit 64 and direction bits 0/1 so clicks, motion and horizontal wheels
/// cannot qualify.
WheelScroll extractWheelScroll(String data) {
  var up = 0;
  var down = 0;
  String strip(String input, Pattern pattern, int Function(Match) rawId) {
    return input.replaceAllMapped(pattern, (match) {
      final id = rawId(match);
      if ((id & 64) == 0) return match.group(0)!;
      switch (id & 3) {
        case 0:
          up++;
        case 1:
          down++;
        default:
          return match.group(0)!; // horizontal wheel: not our axis
      }
      return '';
    });
  }

  // SGR (`ESC[<...`) before urxvt (`ESC[...`) so the `<` form is never
  // misread as a bare-CSI report.
  var rest = strip(data, _x10Wheel, (m) => m.group(1)!.codeUnitAt(0) - 32);
  rest = strip(rest, _sgrWheel, (m) => int.parse(m.group(1)!));
  rest = strip(rest, _urxvtWheel, (m) => int.parse(m.group(1)!) - 32);
  return WheelScroll(up, down, rest);
}

/// Preserve consecutive same-direction wheel reports as source-order runs, using the first
/// exact report bytes for each run. Share encoding and identity rules with extractWheelScroll.
List<({bool towardOlder, int rows, String report})> _wheelRuns(String data) {
  final found = <({int start, int end, bool towardOlder, String bytes})>[];
  void scan(Pattern pattern, int Function(Match) rawId) {
    for (final match in pattern.allMatches(data)) {
      final id = rawId(match);
      if ((id & 64) == 0) continue;
      if ((id & 3) == 0) {
        found.add((
          start: match.start,
          end: match.end,
          towardOlder: true,
          bytes: match.group(0)!
        ));
      } else if ((id & 3) == 1) {
        found.add((
          start: match.start,
          end: match.end,
          towardOlder: false,
          bytes: match.group(0)!
        ));
      }
    }
  }

  scan(_x10Wheel, (m) => m.group(1)!.codeUnitAt(0) - 32);
  scan(_sgrWheel, (m) => int.parse(m.group(1)!));
  scan(_urxvtWheel, (m) => int.parse(m.group(1)!) - 32);
  // Source order across encodings; a match starting inside an accepted
  // one is a misread of its bytes, never a second report.
  found.sort((a, b) => a.start.compareTo(b.start));
  final runs = <({bool towardOlder, int rows, String report})>[];
  var acceptedEnd = -1;
  for (final report in found) {
    if (report.start < acceptedEnd) continue;
    acceptedEnd = report.end;
    if (runs.isNotEmpty && runs.last.towardOlder == report.towardOlder) {
      final last = runs.removeLast();
      runs.add((
        towardOlder: last.towardOlder,
        rows: last.rows + 1,
        report: last.report
      ));
    } else {
      runs.add(
          (towardOlder: report.towardOlder, rows: 1, report: report.bytes));
    }
  }
  return runs;
}
