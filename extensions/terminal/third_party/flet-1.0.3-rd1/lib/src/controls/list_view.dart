import 'package:flutter/material.dart';

import '../extensions/control.dart';
import '../models/control.dart';
import '../utils/edge_insets.dart';
import '../utils/keys.dart';
import '../utils/layout.dart';
import '../utils/misc.dart';
import '../utils/numbers.dart';
import '../widgets/error.dart';
import 'base_controls.dart';
import 'control_widget.dart';
import 'scroll_notification_control.dart';
import 'scrollable_control.dart';

class ListViewControl extends StatefulWidget {
  final Control control;

  ListViewControl({Key? key, required this.control})
      : super(key: key ?? ValueKey("control_${control.id}"));

  @override
  State<ListViewControl> createState() => _ListViewControlState();
}

class _ListViewControlState extends State<ListViewControl> {
  late final ScrollController _controller;

  @override
  void initState() {
    super.initState();
    _controller = ScrollController();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    debugPrint("ListViewControl build: ${widget.control.id}");
    var horizontal = widget.control.getBool("horizontal", false)!;
    var spacing = widget.control.getDouble("spacing", 0)!;
    var dividerThickness = widget.control.getDouble("divider_thickness", 0)!;
    var itemExtent = widget.control.getDouble("item_extent");
    var cacheExtent = widget.control.getDouble("cache_extent");
    var semanticChildCount = widget.control.getInt("semantic_child_count");
    var padding = widget.control.getPadding("padding");
    var reverse = widget.control.getBool("reverse", false)!;
    var clipBehavior =
        widget.control.getClipBehavior("clip_behavior", Clip.hardEdge)!;
    var scrollDirection = horizontal ? Axis.horizontal : Axis.vertical;
    var buildControlsOnDemand =
        widget.control.getBool("build_controls_on_demand", true)!;
    var firstItemPrototype =
        widget.control.getBool("first_item_prototype", false)!;
    var controls = widget.control.children("controls");
    // RelayDeck: a caller that knows the main-axis extent of every child sends
    // "item_extents" (one per child). Each child is then placed at the sum of
    // the extents before it, so a window of children that slides never inherits
    // the offset a child at that index had before; the children are laid out
    // unbounded along the main axis so each can report its natural size.
    final itemExtents = _placedExtents(
        widget.control.get("item_extents"), controls.length);
    var prototypeItem = widget.control.buildWidget("prototype_item") ??
        (firstItemPrototype && controls.isNotEmpty
            ? ControlWidget(control: controls.first)
            : null);

    Widget listView = LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        debugPrint("ListView constraints.maxWidth: ${constraints.maxWidth}");
        debugPrint("ListView constraints.maxHeight: ${constraints.maxHeight}");

        var shrinkWrap =
            (!horizontal && constraints.maxHeight == double.infinity) ||
                (horizontal && constraints.maxWidth == double.infinity);
        Widget child = !buildControlsOnDemand
            ? ListView(
                controller: _controller,
                cacheExtent: cacheExtent,
                reverse: reverse,
                clipBehavior: clipBehavior,
                scrollDirection: scrollDirection,
                shrinkWrap: shrinkWrap,
                padding: padding,
                semanticChildCount: semanticChildCount,
                itemExtent: spacing > 0 ? null : itemExtent,
                prototypeItem: spacing > 0 ? null : prototypeItem,
                children: () {
                  final childWidgets = <Widget>[];
                  for (var index = 0; index < controls.length; index++) {
                    final item = controls[index];
                    childWidgets.add(ControlWidget(
                      key: ValueKey(item.getKey("key")?.value ?? item.id),
                      control: item,
                    ));
                    if (spacing > 0 && index < controls.length - 1) {
                      childWidgets.add(horizontal
                          ? dividerThickness == 0
                              ? SizedBox(width: spacing)
                              : VerticalDivider(
                                  width: spacing, thickness: dividerThickness)
                          : dividerThickness == 0
                              ? SizedBox(height: spacing)
                              : Divider(
                                  height: spacing,
                                  thickness: dividerThickness));
                    }
                  }
                  return childWidgets;
                }(),
              )
            : spacing > 0
                ? ListView.separated(
                    controller: _controller,
                    cacheExtent: cacheExtent,
                    reverse: reverse,
                    clipBehavior: clipBehavior,
                    scrollDirection: scrollDirection,
                    shrinkWrap: shrinkWrap,
                    padding: padding,
                    itemCount: controls.length,
                    itemBuilder: (context, index) {
                      return ControlWidget(
                        key: ValueKey(controls[index].getKey("key")?.value ??
                            controls[index].id),
                        control: controls[index],
                      );
                    },
                    separatorBuilder: (context, index) {
                      return horizontal
                          ? dividerThickness == 0
                              ? SizedBox(width: spacing)
                              : VerticalDivider(
                                  width: spacing, thickness: dividerThickness)
                          : dividerThickness == 0
                              ? SizedBox(height: spacing)
                              : Divider(
                                  height: spacing, thickness: dividerThickness);
                    },
                  )
                : ListView.custom(
                    controller: _controller,
                    clipBehavior: clipBehavior,
                    semanticChildCount: semanticChildCount ?? controls.length,
                    reverse: reverse,
                    cacheExtent: cacheExtent,
                    scrollDirection: scrollDirection,
                    shrinkWrap: shrinkWrap,
                    padding: padding,
                    itemExtent: itemExtents == null ? itemExtent : null,
                    itemExtentBuilder: itemExtents == null
                        ? null
                        : (index, dimensions) => index < itemExtents.length
                            ? itemExtents[index]
                            : null,
                    prototypeItem: itemExtents == null ? prototypeItem : null,
                    childrenDelegate: _childrenDelegate(controls,
                        horizontal: horizontal, itemExtents: itemExtents),
                  );

        if (horizontal &&
            constraints.maxHeight == double.infinity &&
            widget.control.getDouble("height") == null &&
            widget.control.getExpand("expand", 0)! <= 0) {
          return const ErrorControl(
              "Error displaying ListViewControl: height is unbounded.",
              description:
                  "Set a fixed height, a non-zero expand, or place inside "
                  "a control with bounded height.");
        }

        child = ScrollableControl(
            control: widget.control,
            scrollDirection: horizontal ? Axis.horizontal : Axis.vertical,
            scrollController: _controller,
            child: child);

        if (widget.control.hasEventHandler("scroll")) {
          child =
              ScrollNotificationControl(control: widget.control, child: child);
        }

        return child;
      },
    );

    return LayoutControl(control: widget.control, child: listView);
  }
}

/// The children of the on-demand list: the delegate `ListView.builder` makes,
/// or, when every child has an extent, one that also reports the list's whole
/// extent as the sum of them. Flutter otherwise estimates that extent from the
/// children it has laid out, so a list whose far children are much taller (a
/// windowed list's trailing space) ends short until they are built, and a
/// fling or a scrollbar stops early.
SliverChildBuilderDelegate _childrenDelegate(List<Control> controls,
    {required bool horizontal, required List<double>? itemExtents}) {
  Widget build(BuildContext context, int index) {
    Widget item = ControlWidget(
      key: ValueKey(controls[index].getKey("key")?.value ?? controls[index].id),
      control: controls[index],
    );
    if (itemExtents != null) {
      item = OverflowBox(
        alignment: AlignmentDirectional.topStart,
        minWidth: horizontal ? 0 : null,
        maxWidth: horizontal ? double.infinity : null,
        minHeight: horizontal ? null : 0,
        maxHeight: horizontal ? null : double.infinity,
        child: item,
      );
    }
    return item;
  }

  if (itemExtents == null) {
    return SliverChildBuilderDelegate(build, childCount: controls.length);
  }
  return _PlacedChildren(build,
      count: controls.length,
      extent: itemExtents.fold(0.0, (sum, extent) => sum + extent));
}

/// A builder delegate whose scroll extent is known: the sum of the extents.
class _PlacedChildren extends SliverChildBuilderDelegate {
  _PlacedChildren(super.builder, {required int count, required this.extent})
      : super(childCount: count);

  final double extent;

  @override
  double? estimateMaxScrollOffset(int firstIndex, int lastIndex,
          double leadingScrollOffset, double trailingScrollOffset) =>
      extent;
}

/// The extents sent for the children, or null when there is not one per child.
List<double>? _placedExtents(dynamic value, int count) {
  if (value is! List || value.length != count || count == 0) {
    return null;
  }
  final extents = <double>[];
  for (final item in value) {
    final extent = parseDouble(item);
    if (extent == null || !extent.isFinite || extent < 0) {
      return null;
    }
    extents.add(extent);
  }
  return extents;
}
