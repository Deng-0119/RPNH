"""Download official JB sources and prepare one registered text task packet."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from html import unescape
import json
from pathlib import Path
import re
import shutil
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent
ARTICLE_URL = (
    "https://journals.plos.org/plosntds/article/file?"
    "id=10.1371/journal.pntd.0005725&type=manuscript")
SAP_URL = (
    "https://journals.plos.org/plosntds/article/file?"
    "id=10.1371%2Fjournal.pntd.0005725.s002&type=supplementary")
WORKBOOK_URL = (
    "https://journals.plos.org/plosntds/article/file?"
    "id=10.1371%2Fjournal.pntd.0005725.s005&type=supplementary")
XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={
        "User-Agent": "RPNH-public-example/1.0 (+https://github.com/Deng-0119/RPNH)",
    })
    with urlopen(request, timeout=120) as response:
        destination.write_bytes(response.read())


def _jats_text(path: Path) -> str:
    root = ET.fromstring(path.read_bytes())
    lines: list[str] = []
    for element in root.iter():
        if _local(element.tag) not in {"article-title", "title", "p", "th", "td"}:
            continue
        value = _clean("".join(element.itertext()))
        if value and (not lines or lines[-1] != value):
            lines.append(value)
    if not lines:
        raise ValueError("article XML contained no readable text")
    return "\n\n".join(lines) + "\n"


def _docx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    lines: list[str] = []
    for paragraph in root.iter():
        if _local(paragraph.tag) != "p":
            continue
        value = _clean("".join(
            node.text or "" for node in paragraph.iter()
            if _local(node.tag) in {"t", "tab", "br"}))
        if value:
            lines.append(value)
    if not lines:
        raise ValueError("SAP DOCX contained no readable text")
    return "\n".join(lines) + "\n"


def _shared_strings(archive: ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        _clean("".join(node.text or "" for node in item.iter()
                       if _local(node.tag) == "t"))
        for item in root
    ]


def _date_styles(archive: ZipFile) -> set[int]:
    try:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    except KeyError:
        return set()
    custom: dict[int, str] = {}
    for node in root.iter():
        if _local(node.tag) == "numFmt":
            custom[int(node.attrib["numFmtId"])] = node.attrib.get(
                "formatCode", "")
    date_ids = set(range(14, 23)) | {45, 46, 47}
    styles: set[int] = set()
    cell_xfs = next(
        (node for node in root.iter() if _local(node.tag) == "cellXfs"),
        None)
    if cell_xfs is None:
        return styles
    for index, style in enumerate(cell_xfs):
        number_format = int(style.attrib.get("numFmtId", "0"))
        code = re.sub(r'"[^"]*"|\\.|\[[^]]*\]', "", custom.get(
            number_format, "")).lower()
        if number_format in date_ids or (
                "y" in code and ("d" in code or "m" in code)):
            styles.add(index)
    return styles


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    if letters is None:
        raise ValueError(f"invalid worksheet cell reference: {reference}")
    value = 0
    for letter in letters.group():
        value = value * 26 + ord(letter) - ord("A") + 1
    return value - 1


def _cell_value(
        cell: ET.Element, shared: list[str], date_styles: set[int],
) -> str:
    kind = cell.attrib.get("t")
    raw_node = next(
        (node for node in cell if _local(node.tag) == "v"), None)
    raw = "" if raw_node is None or raw_node.text is None else raw_node.text
    if kind == "s" and raw:
        return shared[int(raw)]
    if kind == "inlineStr":
        return _clean("".join(
            node.text or "" for node in cell.iter()
            if _local(node.tag) == "t"))
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    style = int(cell.attrib.get("s", "0"))
    if raw and style in date_styles:
        moment = datetime(1899, 12, 30) + timedelta(days=float(raw))
        return moment.date().isoformat()
    return _clean(raw)


def _xlsx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(
            archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationships
        }
        shared = _shared_strings(archive)
        date_styles = _date_styles(archive)
        sections: list[str] = []
        for sheet in workbook.iter(f"{{{XML_NS}}}sheet"):
            name = sheet.attrib["name"]
            target = targets[sheet.attrib[f"{{{REL_NS}}}id"]]
            member = target.lstrip("/")
            if not member.startswith("xl/"):
                member = "xl/" + member
            root = ET.fromstring(archive.read(member))
            rows: list[list[str]] = []
            for row in root.iter(f"{{{XML_NS}}}row"):
                values: dict[int, str] = {}
                for cell in row.findall(f"{{{XML_NS}}}c"):
                    values[_column_index(cell.attrib["r"])] = _cell_value(
                        cell, shared, date_styles)
                if values:
                    rows.append([
                        values.get(index, "")
                        for index in range(max(values) + 1)
                    ])
            sections.append(f"### SHEET: {name}")
            sections.extend("\t".join(
                value.replace("\t", " ").replace("\n", " ")
                for value in row).rstrip() for row in rows)
    if not sections:
        raise ValueError("workbook contained no readable worksheets")
    return "\n".join(sections) + "\n"


def prepare(
        output_dir: Path, *, article: Path | None = None,
        sap: Path | None = None, workbook: Path | None = None,
) -> Path:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ValueError("--output-dir must be absent")
    supplied = (article, sap, workbook)
    if any(value is not None for value in supplied) and not all(
            value is not None for value in supplied):
        raise ValueError("provide all three local source files or none")
    raw = output_dir / "raw"
    raw.mkdir(parents=True)
    article_path = raw / "article.xml"
    sap_path = raw / "sap.docx"
    workbook_path = raw / "workbook.xlsx"
    if article is None:
        _download(ARTICLE_URL, article_path)
        _download(SAP_URL, sap_path)
        _download(WORKBOOK_URL, workbook_path)
    else:
        assert sap is not None and workbook is not None
        for source, destination in (
                (article, article_path), (sap, sap_path),
                (workbook, workbook_path)):
            if not source.is_file():
                raise ValueError(f"source file does not exist: {source}")
            shutil.copyfile(source, destination)

    article_text = _jats_text(article_path)
    sap_text = _docx_text(sap_path)
    workbook_text = _xlsx_text(workbook_path)
    (output_dir / "article.txt").write_text(article_text, encoding="utf-8")
    (output_dir / "sap.txt").write_text(sap_text, encoding="utf-8")
    (output_dir / "workbook.tsv").write_text(
        workbook_text, encoding="utf-8")
    task = (HERE / "task.md").read_text(encoding="utf-8")
    sources = json.loads((HERE / "sources.json").read_text(encoding="utf-8"))
    prompt = (
        task
        + "\n\n--- BEGIN SOURCE MANIFEST ---\n"
        + json.dumps(sources, ensure_ascii=False, indent=2)
        + "\n--- END SOURCE MANIFEST ---\n"
        + "\n--- BEGIN ARTICLE TEXT ---\n" + article_text
        + "--- END ARTICLE TEXT ---\n"
        + "\n--- BEGIN STATISTICAL ANALYSIS PLAN ---\n" + sap_text
        + "--- END STATISTICAL ANALYSIS PLAN ---\n"
        + "\n--- BEGIN WORKBOOK TSV ---\n" + workbook_text
        + "--- END WORKBOOK TSV ---\n"
    )
    prompt_path = output_dir / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--article", type=Path)
    parser.add_argument("--sap", type=Path)
    parser.add_argument("--workbook", type=Path)
    args = parser.parse_args()
    prompt = prepare(
        args.output_dir, article=args.article, sap=args.sap,
        workbook=args.workbook)
    print(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
