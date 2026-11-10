// AG Studio theme for the Insights dashboard. Every colour points at a CSS variable from app/globals.css, the
// same tokens the rest of RefundPilot uses, so widgets, grids, charts and the edit panels read as part of the
// app and follow it if a token changes.

import { studioTheme } from "ag-studio";

const token = (name: string) => `var(--insights-${name})`;
const line = (color: string) => ({ width: 1, color: token(color) });

// Chart series colours, in order. Outcomes sort alphabetically, so the first five are picked to keep
// "Full refund" on the brand blue and the two "no money moves" outcomes on calm neutrals.
const palette = Object.fromEntries(
  Array.from({ length: 10 }, (_, i) => [
    [`chartPaletteFills${i + 1}Color`, token(`chart-${i + 1}`)],
    [`chartPaletteStrokes${i + 1}Color`, token(`chart-${i + 1}`)],
  ]).flat(),
);

export const insightsTheme = studioTheme.withParams({
  fontFamily: token("font"),
  fontSize: 14,
  textColor: token("ink"),
  foregroundColor: token("ink"),
  subtleTextColor: token("muted"),
  backgroundColor: token("surface"),
  borderColor: token("border"),
  accentColor: token("brand"),
  iconColor: token("brand-dark"),
  rowHoverColor: token("highlight"),

  // The canvas is the page's own background; each widget is one of the app's white cards.
  studioWrapperBorder: false,
  studioWrapperBorderRadius: 0,
  studioWrapperSpacing: 0,
  studioWrapperBackgroundColor: token("canvas"),
  studioCanvasBorder: false,
  studioCanvasBorderRadius: 0,
  studioCanvasShadow: false,
  studioCanvasBackgroundColor: token("canvas"),
  studioCanvasPadding: 4,
  studioCanvasRowHeight: 18,
  studioCanvasFontFamily: token("font"),

  studioWidgetBackgroundColor: token("surface"),
  studioWidgetBorder: line("border"),
  studioWidgetBorderRadius: 12,
  studioWidgetPadding: 12,
  studioWidgetTitleFontFamily: token("font"),
  studioWidgetTitleFontSize: 14,
  studioWidgetTitleFontWeight: 600,
  studioWidgetTitleTextColor: token("ink"),
  studioWidgetSubtitleFontFamily: token("font"),
  studioWidgetSubtitleFontSize: 12,
  studioWidgetSubtitleTextColor: token("muted"),
  studioWidgetCaptionFontFamily: token("font"),
  studioWidgetCaptionFontSize: 12,
  studioWidgetCaptionTextColor: token("muted"),
  studioWidgetResizeBorderColor: token("brand"),

  studioPanelContainerBackgroundColor: token("surface"),
  studioPanelContainerBorder: line("border"),
  studioPanelContainerBorderRadius: 12,
  studioPanelDividerColor: token("border"),
  studioPanelDividerActiveColor: token("brand"),
  studioPanelSectionBorderColor: token("border"),
  studioPanelHeaderFontFamily: token("font"),
  studioPanelHeaderFontWeight: 600,
  studioPanelChevronButtonHoverBackgroundColor: token("highlight"),
  studioPanelChevronButtonHoverColor: token("brand"),
  studioToggleButtonActiveBackgroundColor: token("highlight"),
  studioToggleButtonActiveBorderColor: token("brand"),
  studioToggleButtonActiveColor: token("brand-dark"),

  gridFontFamily: token("font"),
  gridFontSize: 13,
  gridCellTextColor: token("ink"),
  gridHeaderBackgroundColor: token("header"),
  gridHeaderTextColor: token("muted"),
  gridHeaderFontWeight: 600,
  gridHeaderFontSize: 12,
  gridRowHeight: 38,
  gridRowBorder: line("gridline"),
  gridHeaderRowBorder: line("border"),
  gridPinnedRowBorder: line("border"),
  gridRowHoverColor: token("highlight"),
  gridWrapperBorder: false,

  chartFontFamily: token("font"),
  chartTextColor: token("ink"),
  chartSubtleTextColor: token("muted"),
  chartAxisLineColor: token("border"),
  chartGridLineColor: token("gridline"),
  ...palette,
});
