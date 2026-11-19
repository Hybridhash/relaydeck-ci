# Vendored Flutter packages

Keep these packages in place: extension and build configuration refer to their
directories. Each tree retains its upstream license. The patch scripts reconstruct
the shipped files from pristine input plus the recorded diff; do not edit a
vendored tree without updating that diff.

| Package | Upstream version | License | Reason | Patch |
| --- | --- | --- | --- | --- |
| [Flet](flet-1.0.3-rd1/pubspec.yaml) | 1.0.3 | [Apache-2.0](flet-1.0.3-rd1/LICENSE) | Reconcile reconnecting controls by ID, bound retries, disable selection magnifiers, honor per-row list extents and deliver trailing scroll notifications. | [Flet diff](../../../patches/flet-reconnect-merge-patch/flet-1.0.3-rd1.diff) |
| [file_picker](file_picker-11.0.3-rd1/pubspec.yaml) | 11.0.3 | [MIT](file_picker-11.0.3-rd1/LICENSE) | Backport the web upload fix from upstream pull request 2228 so picture attachment completes in WebKit. | [file_picker diff](../../../patches/flutter-file-upload-patch/file_picker-11.0.3-rd1.diff) |

See [patch application and checks](../../../patches/README.md) and the
[license inventory](../../../docs/acknowledgements.md#3-vendored-and-patched-third-party-code).
Remove a patch when the pinned upstream version provides the same behavior and
the extension tests pass.
