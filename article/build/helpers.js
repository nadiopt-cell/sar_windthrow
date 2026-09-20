// helpers.js — построители блоков статьи (формат воспроизводит утверждённую версию 2026-09-21)
const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell,
  WidthType, BorderStyle, HeadingLevel, AlignmentType, Footer, PageNumber, NumberFormat,
} = require('docx');

const FONT = 'Times New Roman';
const FONT_ALL = { ascii: FONT, cs: FONT, eastAsia: FONT, hAnsi: FONT };
const NOB = { style: BorderStyle.NONE, color: 'FFFFFF', size: 0 };

function run(text, o = {}) {
  return new TextRun({
    text,
    font: FONT_ALL,
    color: o.color || '000000',
    size: o.size || 24,
    bold: !!o.bold,
    italics: !!o.italics,
  });
}

// абзац тела: 12 pt, интервал 1,5, отступ 709, выключка
function pBody(text) {
  return new Paragraph({
    spacing: { after: 0, line: 360 },
    indent: { firstLine: 709 },
    alignment: AlignmentType.JUSTIFIED,
    children: [run(text)],
  });
}

// абзац с жирной затравкой (Аннотация., Ключевые слова:)
function pLead(lead, rest, o = {}) {
  return new Paragraph({
    spacing: { after: o.after != null ? o.after : 160, line: 360 },
    alignment: AlignmentType.JUSTIFIED,
    children: [run(lead, { bold: true }), run(rest)],
  });
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    keepNext: true,
    spacing: { before: 420, after: 280, line: 360 },
    alignment: AlignmentType.CENTER,
    children: [run(text, { size: 32, bold: true })],
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    keepNext: true,
    spacing: { before: 320, after: 200, line: 360 },
    children: [run(text, { size: 28, bold: true })],
  });
}

function titlePara(text) {
  return new Paragraph({
    spacing: { after: 240, line: 460, lineRule: 'atLeast' },
    alignment: AlignmentType.CENTER,
    children: [run(text, { size: 36, bold: true })],
  });
}

function centered(text, o = {}) {
  return new Paragraph({
    spacing: { after: o.after != null ? o.after : 120, line: o.line || 360 },
    alignment: AlignmentType.CENTER,
    children: [run(text, o)],
  });
}

// формула: отдельная строка, курсив, до/после 160, по центру; parts — [{t, sub}]
function formulaPara(parts) {
  return new Paragraph({
    spacing: { before: 160, after: 160, line: 360 },
    alignment: AlignmentType.CENTER,
    children: parts.map(pt => new TextRun({
      text: pt.t, font: FONT_ALL, color: '000000', size: 24,
      italics: true, subScript: !!pt.sub,
    })),
  });
}

// подпись таблицы: 10,5 pt, keepNext, before 260 after 120 line 300
function tableCap(text) {
  return new Paragraph({
    keepNext: true,
    spacing: { before: 260, after: 120, line: 300 },
    alignment: AlignmentType.JUSTIFIED,
    children: [run(text, { size: 21 })],
  });
}

// рисунок + подпись
function figure(mediaDir, src, w, h, cap) {
  const img = new Paragraph({
    keepNext: true,
    spacing: { before: 260, after: 100 },
    alignment: AlignmentType.CENTER,
    children: [new ImageRun({
      type: 'png',
      data: fs.readFileSync(path.join(mediaDir, src)),
      transformation: { width: w, height: h },
    })],
  });
  const capP = new Paragraph({
    spacing: { after: 260, line: 300 },
    alignment: AlignmentType.JUSTIFIED,
    children: [run(cap, { size: 21 })],
  });
  return [img, capP];
}

function cell(text, o = {}) {
  const borders = o.headerBottom
    ? { bottom: { style: BorderStyle.SINGLE, color: '000000', size: 2 } }
    : undefined;
  return new TableCell({
    width: { size: o.w, type: WidthType.DXA },
    borders,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: [new Paragraph({
      spacing: { line: 276 },
      alignment: (o.align === 'left') ? AlignmentType.LEFT : AlignmentType.CENTER,
      children: [run(text, { size: 18, bold: !!o.bold, italics: !!o.italics })],
    })],
  });
}

// трёхлинейная таблица; aligns — массив 'c'|'l' по колонкам; meanRow — курсивная строка
function threeLineTable({ grid, header, rows, aligns, meanRow }) {
  const dataRows = rows.map(r => new TableRow({
    cantSplit: true,
    children: r.map((t, i) => cell(t, { w: grid[i], align: aligns[i] })),
  }));
  const allRows = [
    new TableRow({
      cantSplit: true,
      tableHeader: true,
      children: header.map((t, i) => cell(t, { w: grid[i], bold: true, headerBottom: true, align: aligns[i] })),
    }),
    ...dataRows,
  ];
  if (meanRow) {
    allRows.push(new TableRow({
      cantSplit: true,
      children: meanRow.map((t, i) => cell(t, { w: grid[i], italics: true, align: aligns[i] })),
    }));
  }
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: {
      top: { style: BorderStyle.SINGLE, color: '000000', size: 6 },
      bottom: { style: BorderStyle.SINGLE, color: '000000', size: 6 },
      left: NOB, right: NOB, insideHorizontal: NOB, insideVertical: NOB,
    },
    rows: allRows,
  });
}

// источник литературы: 10,5 pt, висячий отступ 420
function litItem(text) {
  return new Paragraph({
    spacing: { after: 40, line: 300 },
    indent: { left: 420, hanging: 420 },
    alignment: AlignmentType.JUSTIFIED,
    children: [run(text, { size: 21 })],
  });
}

function blocksToChildren(blocks, mediaDir) {
  const out = [];
  for (const b of blocks) {
    if (b.h1 != null) out.push(h1(b.h1));
    else if (b.h2 != null) out.push(h2(b.h2));
    else if (b.p != null) out.push(pBody(b.p));
    else if (b.lead) out.push(pLead(b.lead[0], b.lead[1]));
    else if (b.tc != null) out.push(tableCap(b.tc));
    else if (b.fig) out.push(...figure(mediaDir, b.fig.src, b.fig.w, b.fig.h, b.fig.cap));
    else if (b.formula) out.push(formulaPara(b.formula));
    else if (b.table) out.push(threeLineTable(b.table));
    else if (b.raw) out.push(b.raw);
  }
  return out;
}

async function buildDoc(children, outPath) {
  const footer = new Footer({
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: [PageNumber.CURRENT], font: FONT_ALL, color: '000000', size: 21 })],
    })],
  });
  const doc = new Document({
    styles: { default: { document: {
      run: { font: FONT_ALL, size: 24, color: '000000' },
      paragraph: { spacing: { line: 360 } },
    } } },
    sections: [{
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, bottom: 1440, left: 1701, right: 1417, header: 850, footer: 992 },
          pageNumbers: { start: 1, formatType: NumberFormat.DECIMAL },
        },
      },
      footers: { default: footer },
      children,
    }],
  });
  const buf = await Packer.toBuffer(doc);
  fs.writeFileSync(outPath, buf);
  return outPath;
}

module.exports = { buildDoc, blocksToChildren, run, centered, titlePara, litItem, formulaPara };
