// build.js — сборка статьи v2 (утверждённая версия 2026-09-21 + правки по ответам рецензенту A–E)
const path = require('path');
const { buildDoc, blocksToChildren } = require('./helpers');

// Пути относительные: media/ и готовый docx лежат рядом со script в каталоге article/
const MEDIA_DIR = path.join(__dirname, '..', 'media');
const OUT = path.join(__dirname, '..', 'Статья_Выявление_ветровалов_Sentinel-1_2026-09-21_v2_правки_A-E.docx');

(async () => {
  const blocks = [
    ...require('./content_a'),
    ...require('./content_b'),
    ...require('./content_c'),
  ];
  const children = blocksToChildren(blocks, MEDIA_DIR);
  await buildDoc(children, OUT);
  console.log('OK:', OUT);
})().catch(e => { console.error(e); process.exit(1); });
