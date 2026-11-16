#!/usr/bin/env python3
"""Build a consolidated Paramount analysis PowerPoint from package manifests.

Usage:
    python Paramount_PowerPoint_Builder.py --manifest <presentation_manifest.json>
    python Paramount_PowerPoint_Builder.py --write-example-package <folder>

The unified analysis launcher creates the presentation manifest and one package
folder per analysis. Each completed analysis writes package_folder/manifest.json
last. This builder tolerates missing or invalid packages and reports them in the
deck rather than discarding the completed analyses.

Runtime requirements:
    Python 3.9+
    Node.js
    PptxGenJS available to Node (npm install pptxgenjs)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PACKAGE_MANIFEST_NAME = "manifest.json"
DEFAULT_OUTPUT_NAME = "Paramount_Analysis_Results.pptx"
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_SLIDE_TYPES = {"summary", "image", "table", "failures", "section", "text"}
DEFAULT_THEME_FILENAME = "S3RTransparencyPPTXTheme.thmx"
DEFAULT_THEME_FOLDER_NAME = "Presentation Themes"
DRAWINGML_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"

EMBEDDED_JS = r'''"use strict";
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const payloadPath = process.argv[2];
const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));
const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = payload.operator || "Paramount Analysis";
pptx.subject = payload.title;
pptx.title = payload.title;
pptx.company = "Advanced Energy";
pptx.lang = "en-US";
const theme = payload.theme || {};
const themeColors = theme.colors || {};
const F = {
  heading: theme.heading_font || "Aptos Display",
  body: theme.body_font || "Aptos"
};
const C = {
  blue: themeColors.accent1 || "0078D4",
  navy: themeColors.dark2 || "17365D",
  ink: themeColors.dark1 || "172B4D",
  gray: themeColors.accent6 || "667085",
  light: themeColors.light2 || "EAF2F8",
  pale: themeColors.light1 || "F7F9FC",
  green: themeColors.accent2 || "107C10",
  red: themeColors.accent3 || "C50F1F",
  amber: themeColors.accent4 || "986F0B",
  white: themeColors.light1 || "FFFFFF",
  line: themeColors.accent5 || "D0D5DD",
  hyperlink: themeColors.hyperlink || "0563C1",
  followedHyperlink: themeColors.followed_hyperlink || "954F72"
};
pptx.theme = {
  headFontFace: F.heading,
  bodyFontFace: F.body,
  lang: "en-US"
};
pptx.defineSlideMaster({
  title: "PARAMOUNT",
  background: { color: C.pale },
  objects: [
    { rect: { x: 0, y: 0, w: 13.333, h: 0.13, fill: { color: C.blue }, line: { color: C.blue } } },
    { text: { text: payload.footer || "Paramount Analysis", options: { x: 0.45, y: 7.12, w: 11.8, h: 0.18, fontFace: F.body, fontSize: 8, color: C.gray, margin: 0 } } },
    { text: { text: "", options: { x: 12.2, y: 7.08, w: 0.55, h: 0.2, fontFace: F.body, fontSize: 8, color: C.gray, align: "right", margin: 0, isSlideNumber: true } } }
  ],
  slideNumber: { x: 12.2, y: 7.08, w: 0.55, h: 0.2, color: C.gray, fontSize: 8, align: "right" }
});
function addTitle(slide, title, subtitle="") {
  slide.addText(title, { x:0.55, y:0.38, w:12.1, h:0.45, fontFace:F.heading, fontSize:24, bold:true, color:C.navy, margin:0, breakLine:false });
  if (subtitle) slide.addText(subtitle, { x:0.57, y:0.88, w:12.0, h:0.28, fontFace:F.body, fontSize:10.5, color:C.gray, margin:0 });
}
function addNotes(slide, sourceLines) {
  if (!sourceLines || !sourceLines.length) return;
  slide.addNotes(`[Sources]\n${sourceLines.join("\n")}\n[/Sources]`);
}
function statusColor(status) {
  const s = String(status || "").toLowerCase();
  if (s.includes("complete") || s.includes("ready") || s.includes("success")) return C.green;
  if (s.includes("fail") || s.includes("invalid") || s.includes("mismatch")) return C.red;
  if (s.includes("skip") || s.includes("not found") || s.includes("stopped")) return C.amber;
  return C.gray;
}
function addTitleSlide() {
  const slide = pptx.addSlide();
  slide.background = { color:C.navy };
  slide.addShape(pptx.ShapeType.rect, { x:0, y:0, w:0.22, h:7.5, fill:{color:C.blue}, line:{color:C.blue} });
  slide.addText(payload.title, { x:0.85, y:1.55, w:11.5, h:1.1, fontFace:F.heading, fontSize:34, bold:true, color:C.white, margin:0, valign:"mid", fit:"shrink" });
  if (payload.subtitle) slide.addText(payload.subtitle, { x:0.88, y:2.8, w:10.8, h:0.55, fontSize:18, color:C.light, margin:0, fit:"shrink" });
  const meta = [payload.project_name, payload.dut_name ? `DUT: ${payload.dut_name}` : "", payload.operator ? `Operator: ${payload.operator}` : "", payload.created].filter(Boolean).join("\n");
  slide.addText(meta, { x:0.9, y:4.5, w:7.2, h:1.25, fontSize:14, color:C.light, margin:0.02, breakLine:false, fit:"shrink" });
  slide.addText("PARAMOUNT", { x:9.9, y:6.25, w:2.4, h:0.4, fontSize:18, bold:true, color:C.white, align:"right", margin:0 });
}
function addStatusSlide() {
  const slide = pptx.addSlide("PARAMOUNT"); addTitle(slide, "Analysis completion status", `${payload.analyses.length} selected analyses`);
  const rows = [["Analysis", "Execution status", "Presentation package"]];
  payload.analyses.forEach(a => rows.push([a.display_name, a.status || "Unknown", a.package_status || "Not checked"]));
  slide.addTable(rows, { x:0.65, y:1.35, w:12.0, h:5.2, border:{type:"solid", color:C.line, pt:0.7},
    fill:C.white, color:C.ink, fontFace:F.body, fontSize:11, margin:0.08,
    rowH:0.42, colW:[3.2,4.2,4.6],
    bold:false, autoFit:false,
    bandRow:true, bandColor:C.light,
    fillHeader:C.navy, colorHeader:C.white, boldHeader:true });
}
function addExecutiveSummary() {
  const slide = pptx.addSlide("PARAMOUNT"); addTitle(slide, "Executive summary", payload.project_name || payload.dut_name || "Consolidated analysis results");
  const ready = payload.analyses.filter(a => String(a.package_status).toLowerCase().includes("ready")).length;
  const missing = payload.analyses.length - ready;
  const cards = [
    ["Selected analyses", String(payload.analyses.length), C.blue],
    ["Packages ready", String(ready), C.green],
    ["Packages unavailable", String(missing), missing ? C.amber : C.green]
  ];
  cards.forEach((c,i) => { const x=0.75+i*4.15; slide.addShape(pptx.ShapeType.roundRect,{x,y:1.5,w:3.75,h:1.55,rectRadius:0.08,fill:{color:C.white},line:{color:C.line,pt:1},shadow:{type:"outer",color:"AAB2BD",opacity:0.15,blur:1,angle:45,distance:1}}); slide.addText(c[1],{x:x+0.22,y:1.75,w:3.3,h:0.65,fontSize:28,bold:true,color:c[2],align:"center",margin:0}); slide.addText(c[0],{x:x+0.22,y:2.48,w:3.3,h:0.3,fontSize:12,color:C.gray,align:"center",margin:0}); });
  const summaries = payload.packages.flatMap(p => p.highlights || []).slice(0,8);
  const text = summaries.length ? summaries.map(x => ({text:String(x),options:{bullet:{indent:16},breakLine:true}})) : [{text:"Analysis-specific highlights will appear after individual exporters create package manifests.",options:{bullet:{indent:16}}}];
  slide.addText(text,{x:0.85,y:3.55,w:11.5,h:2.7,fontSize:15,color:C.ink,breakLine:false,margin:0.08,fit:"shrink"});
}
function addSectionSlide(pkg) {
  const slide=pptx.addSlide(); slide.background={color:C.navy};
  slide.addShape(pptx.ShapeType.rect,{x:0,y:0,w:0.18,h:7.5,fill:{color:C.blue},line:{color:C.blue}});
  slide.addText(pkg.display_name,{x:0.9,y:2.4,w:11.3,h:0.95,fontSize:32,bold:true,color:C.white,margin:0,fit:"shrink"});
  slide.addText(pkg.dataset_label || pkg.status || "Analysis results",{x:0.93,y:3.55,w:10.8,h:0.45,fontSize:17,color:C.light,margin:0,fit:"shrink"});
}
function addSummarySlide(pkg, spec) {
  const slide=pptx.addSlide("PARAMOUNT"); addTitle(slide,spec.title || `${pkg.display_name} Summary`,spec.subtitle || pkg.dataset_label || "");
  const highlights=(spec.highlights || pkg.highlights || []).slice(0,10);
  const runs=highlights.length ? highlights.map(v=>({text:String(v),options:{bullet:{indent:16},breakLine:true}})) : [{text:"No summary highlights were supplied by this analysis package.",options:{bullet:{indent:16}}}];
  slide.addText(runs,{x:0.75,y:1.35,w:5.4,h:4.9,fontSize:15,color:C.ink,margin:0.1,fit:"shrink"});
  const metrics=spec.metrics || pkg.metrics || {};
  const entries=Object.entries(metrics).slice(0,10);
  if(entries.length){ const rows=[["Metric","Value"],...entries.map(([k,v])=>[String(k),String(v)])]; slide.addTable(rows,{x:6.45,y:1.35,w:6.1,h:4.9,border:{color:C.line,pt:0.7},fill:C.white,fontSize:11,color:C.ink,margin:0.07,bandRow:true,bandColor:C.light,fillHeader:C.navy,colorHeader:C.white,boldHeader:true,colW:[3.65,2.45]}); }
  addNotes(slide,spec.sources || pkg.sources || []);
}
function addImageSlide(pkg,spec) {
  const slide=pptx.addSlide("PARAMOUNT"); addTitle(slide,spec.title || pkg.display_name,spec.subtitle || "");
  const image=spec.resolved_image;
  if(image && fs.existsSync(image)) slide.addImage({path:image,x:0.7,y:1.25,w:11.95,h:5.55,sizing:"contain"});
  else slide.addText(`Image unavailable: ${spec.image_file || "not specified"}`,{x:1.0,y:3.0,w:11.0,h:0.6,fontSize:18,color:C.red,align:"center",margin:0});
  if(spec.caption) slide.addText(spec.caption,{x:0.85,y:6.65,w:11.6,h:0.25,fontSize:9.5,color:C.gray,align:"center",margin:0,fit:"shrink"});
  addNotes(slide,spec.sources || pkg.sources || []);
}
function addTableSlide(pkg,spec) {
  const slide=pptx.addSlide("PARAMOUNT"); addTitle(slide,spec.title || pkg.display_name,spec.subtitle || "");
  const rows=spec.rows || [["Status"],["No table rows supplied"]];
  const maxRows=Math.max(2,Math.min(rows.length, Number(spec.max_rows || 16)));
  const shown=rows.slice(0,maxRows);
  const cols=shown.reduce((m,r)=>Math.max(m,r.length),1); const widths=Array(cols).fill(11.9/cols);
  slide.addTable(shown,{x:0.7,y:1.3,w:11.9,h:5.45,border:{color:C.line,pt:0.6},fill:C.white,color:C.ink,fontFace:F.body,fontSize:Math.max(8,Math.min(12,13-cols*0.5)),margin:0.055,bandRow:true,bandColor:C.light,fillHeader:C.navy,colorHeader:C.white,boldHeader:true,colW:widths,autoFit:false});
  if(rows.length>shown.length) slide.addText(`${rows.length-shown.length} additional rows are available in the analysis workbook.`,{x:0.8,y:6.76,w:11.5,h:0.2,fontSize:8.5,color:C.gray,margin:0});
  addNotes(slide,spec.sources || pkg.sources || []);
}
function addTextSlide(pkg,spec) {
  const slide=pptx.addSlide("PARAMOUNT"); addTitle(slide,spec.title || pkg.display_name,spec.subtitle || "");
  slide.addText(spec.body || "",{x:0.8,y:1.35,w:11.6,h:5.3,fontSize:16,color:C.ink,margin:0.12,breakLine:false,fit:"shrink",valign:"top"});
  addNotes(slide,spec.sources || pkg.sources || []);
}
function addMissingPackageSlide(a) {
  const slide=pptx.addSlide("PARAMOUNT"); addTitle(slide,a.display_name,"Presentation package unavailable");
  slide.addShape(pptx.ShapeType.roundRect,{x:1.2,y:2.0,w:10.9,h:2.4,fill:{color:C.light},line:{color:C.amber,pt:1.2},radius:0.08});
  slide.addText(a.package_status || "Package not found",{x:1.55,y:2.45,w:10.2,h:0.55,fontSize:22,bold:true,color:C.amber,align:"center",margin:0,fit:"shrink"});
  slide.addText("The analysis may have completed normally. This slide indicates only that the analysis-specific PowerPoint exporter has not yet produced a valid package manifest.",{x:1.65,y:3.25,w:10.0,h:0.65,fontSize:13,color:C.ink,align:"center",margin:0.04,fit:"shrink"});
}
function addAppendix() {
  const slide=pptx.addSlide("PARAMOUNT"); addTitle(slide,"Appendix: source locations","Analysis workbooks, package folders, and run metadata");
  const rows=[["Analysis","Package folder","Source folder"]];
  payload.analyses.forEach(a=>rows.push([a.display_name,a.package_folder || "Not available",a.source_folder || "Not reported"]));
  slide.addTable(rows,{x:0.55,y:1.25,w:12.2,h:5.6,border:{color:C.line,pt:0.6},fill:C.white,color:C.ink,fontSize:8.5,margin:0.045,bandRow:true,bandColor:C.light,fillHeader:C.navy,colorHeader:C.white,boldHeader:true,colW:[2.2,5.0,5.0]});
}

if(payload.include_title_slide) addTitleSlide();
addStatusSlide();
if(payload.include_executive_summary) addExecutiveSummary();
for(const analysis of payload.analyses){
  const pkg=payload.packages.find(p=>p.analysis_id===analysis.analysis_id);
  if(!pkg){ addMissingPackageSlide(analysis); continue; }
  addSectionSlide(pkg);
  const slides=(pkg.slides && pkg.slides.length) ? pkg.slides : [{slide_type:"summary",title:`${pkg.display_name} Summary`}];
  for(const spec of slides){
    if(spec.slide_type==="section") addSectionSlide({...pkg,...spec});
    else if(spec.slide_type==="image") addImageSlide(pkg,spec);
    else if(spec.slide_type==="table" || spec.slide_type==="failures") addTableSlide(pkg,spec);
    else if(spec.slide_type==="text") addTextSlide(pkg,spec);
    else addSummarySlide(pkg,spec);
  }
}
if(payload.include_appendix) addAppendix();
pptx.writeFile({fileName:payload.output_path});
'''


def _theme_color(element: ET.Element | None) -> str:
    if element is None:
        return ""
    for child in list(element):
        value = child.get("val") or child.get("lastClr") or ""
        value = value.strip().lstrip("#").upper()
        if re.fullmatch(r"[0-9A-F]{6}", value):
            return value
    return ""


def extract_thmx_theme(theme_path: Path) -> dict[str, Any]:
    """Extract Office theme colors and Latin major/minor fonts from a .thmx file."""
    theme_path = theme_path.expanduser().resolve()
    if not theme_path.is_file():
        raise FileNotFoundError(f"PowerPoint theme file does not exist: {theme_path}")
    if theme_path.suffix.lower() != ".thmx":
        raise ValueError(f"PowerPoint theme file must use the .thmx extension: {theme_path}")
    namespace = {"a": DRAWINGML_NAMESPACE}
    candidates: list[tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(theme_path, "r") as archive:
            for member in archive.namelist():
                if member.lower().endswith(".xml"):
                    candidates.append((member, archive.read(member)))
    except zipfile.BadZipFile as error:
        raise ValueError(f"The selected .thmx file is not a valid Office theme package: {theme_path}") from error
    for member, content in candidates:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            continue
        color_scheme = root.find(".//a:clrScheme", namespace)
        font_scheme = root.find(".//a:fontScheme", namespace)
        if color_scheme is None or font_scheme is None:
            continue
        tags = {
            "dark1": "dk1", "light1": "lt1", "dark2": "dk2", "light2": "lt2",
            "accent1": "accent1", "accent2": "accent2", "accent3": "accent3",
            "accent4": "accent4", "accent5": "accent5", "accent6": "accent6",
            "hyperlink": "hlink", "followed_hyperlink": "folHlink",
        }
        colors = {
            output_name: _theme_color(color_scheme.find(f"a:{xml_name}", namespace))
            for output_name, xml_name in tags.items()
        }
        colors = {key: value for key, value in colors.items() if value}
        major_latin = font_scheme.find("./a:majorFont/a:latin", namespace)
        minor_latin = font_scheme.find("./a:minorFont/a:latin", namespace)
        heading_font = (major_latin.get("typeface", "") if major_latin is not None else "").strip()
        body_font = (minor_latin.get("typeface", "") if minor_latin is not None else "").strip()
        return {
            "name": root.get("name") or color_scheme.get("name") or theme_path.stem,
            "source_file": str(theme_path),
            "internal_xml": member,
            "heading_font": heading_font or "Aptos Display",
            "body_font": body_font or "Aptos",
            "colors": colors,
        }
    raise ValueError(
        f"No DrawingML color and font scheme was found in the selected theme: {theme_path}"
    )


def resolve_theme_file(
    manifest_path: Path,
    presentation: dict[str, Any],
    theme_override: Path | None = None,
) -> Path | None:
    """Resolve theme priority: CLI, environment, manifest, then conventional sibling file."""
    candidates: list[Path] = []
    if theme_override:
        candidates.append(theme_override)
    environment_theme = os.environ.get("PARAMOUNT_POWERPOINT_THEME_FILE", "").strip()
    if environment_theme:
        candidates.append(Path(environment_theme))
    manifest_theme = str(presentation.get("theme_file") or "").strip()
    if manifest_theme:
        candidates.append(Path(manifest_theme))
    builder_folder = Path(__file__).resolve().parent
    candidates.append(builder_folder / DEFAULT_THEME_FILENAME)

    # Automatically search the Presentation Themes folder beside the builder.
    # Prefer the conventional filename when present. Otherwise, use a .thmx file
    # only when the folder contains exactly one candidate, avoiding an ambiguous
    # and potentially unintended theme selection.
    theme_folder = builder_folder / DEFAULT_THEME_FOLDER_NAME
    conventional_folder_theme = theme_folder / DEFAULT_THEME_FILENAME
    candidates.append(conventional_folder_theme)
    if theme_folder.is_dir():
        folder_themes = sorted(
            item for item in theme_folder.iterdir()
            if item.is_file() and item.suffix.lower() == ".thmx"
        )
        if len(folder_themes) == 1:
            candidates.append(folder_themes[0])
        elif len(folder_themes) > 1 and not conventional_folder_theme.is_file():
            theme_names = ", ".join(item.name for item in folder_themes)
            raise ValueError(
                f"Multiple .thmx files were found in {theme_folder}. "
                f"Rename the intended file to {DEFAULT_THEME_FILENAME}, specify --theme, "
                f"set PARAMOUNT_POWERPOINT_THEME_FILE, or add presentation.theme_file. "
                f"Found: {theme_names}"
            )

    seen_candidates = set()
    for candidate in candidates:
        candidate = candidate.expanduser()
        if not candidate.is_absolute():
            candidate = manifest_path.parent / candidate
        resolved_text = str(candidate.resolve())
        if resolved_text in seen_candidates:
            continue
        seen_candidates.add(resolved_text)
        if candidate.is_file():
            return candidate.resolve()
    if theme_override or environment_theme or manifest_theme:
        requested = theme_override or Path(environment_theme or manifest_theme)
        raise FileNotFoundError(f"Requested PowerPoint theme file was not found: {requested}")
    return None


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def _safe_path(base: Path, value: str | os.PathLike[str] | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _sanitized_filename(value: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "_", value).strip().rstrip(".")
    if not name.lower().endswith(".pptx"):
        name += ".pptx"
    return name or DEFAULT_OUTPUT_NAME


def _read_csv_rows(path: Path, max_rows: int = 200) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        return [[str(cell) for cell in row] for _, row in zip(range(max_rows), reader)]


def _normalize_slide(package_folder: Path, slide: dict[str, Any]) -> dict[str, Any]:
    result = dict(slide)
    slide_type = str(result.get("slide_type", "summary")).lower()
    if slide_type not in SUPPORTED_SLIDE_TYPES:
        slide_type = "summary"
    result["slide_type"] = slide_type
    if slide_type == "image":
        image = _safe_path(package_folder, result.get("image_file"))
        result["resolved_image"] = str(image) if image and image.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS else ""
    if slide_type in {"table", "failures"}:
        if not isinstance(result.get("rows"), list):
            table_file = _safe_path(package_folder, result.get("table_file"))
            result["rows"] = _read_csv_rows(table_file) if table_file and table_file.is_file() else []
        result["rows"] = [[str(cell) for cell in row] for row in result.get("rows", []) if isinstance(row, list)]
    result["sources"] = [str(item) for item in result.get("sources", [])]
    return result


def _load_package(run_folder: Path, analysis: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    analysis_id = str(analysis.get("analysis_id", "")).strip()
    if not analysis_id:
        return None, "Missing analysis_id"
    package_folder = run_folder / "analysis_packages" / analysis_id
    analysis["package_folder"] = str(package_folder)
    manifest_path = package_folder / PACKAGE_MANIFEST_NAME
    if not manifest_path.is_file():
        return None, "Package not found"
    try:
        package = _load_json(manifest_path)
        if int(package.get("schema_version", SCHEMA_VERSION)) != SCHEMA_VERSION:
            return None, f"Unsupported package schema version: {package.get('schema_version')}"
        if package.get("analysis_id") != analysis_id:
            return None, "Package manifest analysis_id mismatch"
        package["display_name"] = str(package.get("display_name") or analysis.get("display_name") or analysis_id)
        package["status"] = str(package.get("status", "completed"))
        package["dataset_label"] = str(package.get("dataset_label", ""))
        package["highlights"] = [str(item) for item in package.get("highlights", [])]
        package["metrics"] = {str(key): str(value) for key, value in package.get("metrics", {}).items()}
        package["sources"] = [str(item) for item in package.get("sources", [])]
        package["slides"] = [
            _normalize_slide(package_folder, item)
            for item in package.get("slides", []) if isinstance(item, dict)
        ]
        analysis["source_folder"] = str(package.get("source_folder", ""))
        analysis["package_status"] = "Package ready"
        return package, "Package ready"
    except Exception as error:
        return None, f"Invalid package manifest: {error}"


def build_payload(manifest_path: Path, theme_override: Path | None = None) -> tuple[dict[str, Any], Path]:
    manifest = _load_json(manifest_path)
    if int(manifest.get("schema_version", -1)) != SCHEMA_VERSION:
        raise ValueError(f"Unsupported presentation schema version: {manifest.get('schema_version')}")
    run_folder = _safe_path(manifest_path.parent, manifest.get("run_folder")) or manifest_path.parent.resolve()
    if not run_folder.is_dir():
        raise ValueError(f"Presentation run folder does not exist: {run_folder}")
    presentation = manifest.get("presentation") if isinstance(manifest.get("presentation"), dict) else {}
    theme_file = resolve_theme_file(manifest_path, presentation, theme_override)
    theme_profile = extract_thmx_theme(theme_file) if theme_file else {
        "name": "Built-in Paramount theme",
        "source_file": "",
        "heading_font": "Aptos Display",
        "body_font": "Aptos",
        "colors": {},
    }
    analyses = [dict(item) for item in manifest.get("selected_analyses", []) if isinstance(item, dict)]
    packages: list[dict[str, Any]] = []
    for analysis in analyses:
        package, status = _load_package(run_folder, analysis)
        analysis["package_status"] = status
        if package:
            packages.append(package)
    output_name = _sanitized_filename(str(presentation.get("filename") or DEFAULT_OUTPUT_NAME))
    output_path = run_folder / output_name
    payload = {
        "title": str(presentation.get("title") or "Paramount Analysis Results"),
        "subtitle": str(presentation.get("subtitle") or ""),
        "dut_name": str(presentation.get("dut_name") or ""),
        "project_name": str(presentation.get("project_name") or ""),
        "operator": str(presentation.get("operator") or ""),
        "created": datetime.now().strftime("%B %d, %Y"),
        "footer": str(presentation.get("project_name") or presentation.get("title") or "Paramount Analysis"),
        "include_title_slide": bool(presentation.get("include_title_slide", True)),
        "include_executive_summary": bool(presentation.get("include_executive_summary", True)),
        "include_failure_tables": bool(presentation.get("include_failure_tables", True)),
        "include_appendix": bool(presentation.get("include_appendix", True)),
        "open_after_generation": bool(presentation.get("open_after_generation", False)),
        "output_path": str(output_path),
        "analyses": analyses,
        "packages": packages,
        "theme": theme_profile,
    }
    return payload, output_path


def _find_node() -> str:
    executable = shutil.which("node") or shutil.which("node.exe")
    if not executable:
        raise RuntimeError("Node.js was not found. Install Node.js and PptxGenJS before running the builder.")
    return executable


def build_presentation(manifest_path: Path, theme_override: Path | None = None) -> Path:
    payload, output_path = build_payload(manifest_path.resolve(), theme_override)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    node = _find_node()
    with tempfile.TemporaryDirectory(prefix="paramount_ppt_builder_") as temporary:
        temporary_path = Path(temporary)
        payload_path = temporary_path / "payload.json"
        javascript_path = temporary_path / "build.js"
        payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        javascript_path.write_text(EMBEDDED_JS, encoding="utf-8")
        environment = os.environ.copy()
        existing = environment.get("NODE_PATH", "")
        common_paths = [
            str(Path(__file__).resolve().parent / "node_modules"),
            str(Path(sys.executable).resolve().parent.parent / "node_modules"),
        ]
        environment["NODE_PATH"] = os.pathsep.join([item for item in common_paths + [existing] if item])
        result = subprocess.run(
            [node, str(javascript_path), str(payload_path)],
            cwd=str(output_path.parent),
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"Node exited with code {result.returncode}").strip()
            raise RuntimeError(f"PowerPoint generation failed: {detail}")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"PowerPoint generation did not create the expected file: {output_path}")
    if payload["open_after_generation"] and sys.platform.startswith("win"):
        try:
            os.startfile(output_path)  # type: ignore[attr-defined]
        except OSError:
            pass
    return output_path


def write_example_package(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    table_path = folder / "summary_table.csv"
    table_path.write_text("Metric,Value\nAccepted measurements,155\nFailed limits,0\nCollection cycles,5\n", encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": folder.name,
        "display_name": "Example Analysis",
        "status": "completed",
        "dataset_label": "Configuration A",
        "source_folder": "C:/Example/Analysis",
        "highlights": ["Five collection cycles were analyzed.", "No specification failures were detected."],
        "metrics": {"Accepted measurements": 155, "Failed limits": 0, "Collection cycles": 5},
        "slides": [
            {"slide_type": "summary", "title": "Example Analysis Summary"},
            {"slide_type": "table", "title": "Example Summary Table", "table_file": table_path.name},
        ],
    }
    path = folder / PACKAGE_MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a consolidated Paramount analysis PowerPoint deck.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path, help="Path to presentation_manifest.json")
    group.add_argument("--write-example-package", type=Path, help="Write an example analysis package into this folder")
    parser.add_argument(
        "--theme", type=Path,
        help=("Optional .thmx file. Overrides PARAMOUNT_POWERPOINT_THEME_FILE, "
              "presentation.theme_file, the default S3RTransparencyPPTXTheme.thmx, "
              "and automatic discovery in the Presentation Themes folder."),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        if args.write_example_package:
            path = write_example_package(args.write_example_package)
            print(f"Example package written: {path}")
            return 0
        output = build_presentation(args.manifest, args.theme)
        if args.theme:
            print(f"PowerPoint theme requested: {args.theme.resolve()}")
        print(f"PowerPoint created: {output}")
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
