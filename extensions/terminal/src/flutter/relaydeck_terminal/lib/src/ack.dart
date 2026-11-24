import 'dart:typed_data';

/// Encode cumulative applied-byte acknowledgements using two big-endian u32 halves. This avoids
/// unsupported u64 writes in dart2js.
const kAckKind = 0xFF;

/// Largest exactly-representable ACK value on dart2js, where `int` is
/// a double (2^53 - 1, ~9 PB of terminal output, unreachable in
/// practice). Counter values beyond this saturate AT this bound (see
/// [encodeAck]) — an under-ACK, never an over-ACK.
const kMaxAckBytes = 9007199254740991;

/// Clamp negative byte counts to zero and saturate at the largest exactly representable
/// JavaScript integer. Counts beyond that range require a reset to establish an exact byte
/// base.
Uint8List encodeAck(int applied) {
  var v = applied;
  if (v < 0) v = 0;
  if (v > kMaxAckBytes) v = kMaxAckBytes;
  final int hi = v ~/ 0x100000000;
  final int lo = v % 0x100000000;
  final data = ByteData(9)
    ..setUint8(0, kAckKind)
    ..setUint32(1, hi, Endian.big)
    ..setUint32(5, lo, Endian.big);
  return data.buffer.asUint8List();
}
