# Worklog

---
Task ID: 1
Agent: Super Z (main)
Task: Поиск и верификация научных работ по SAR-детектированию ветровалов (обзор для QGIS-плагина)

Work Log:
- Проведено 22+ web-search запросов (EN/RU) по теме windthrow/windfall SAR detection
- Извлечены детали метода и метрики из полнотекста Rüetschi 2019 (MDPI, page_reader)
- Метаданные верифицированы через Semantic Scholar API и Crossref API
- Результаты сохранены в /home/z/my-project/research/*.json

Stage Summary:
- Собрано 12 SAR-источников: Fransson 2010 (IGARSS, Швеция, 3 сенсора), Eriksson 2012 (IGARSS, физика сигнатур), Thiele 2012 (IGARSS, TerraSAR-X), Tanase 2018 (RSE 209:700-711, ALOS PALSAR, OA 69-84%), Rüetschi 2019 (RS 11:115, Sentinel-1, PA 0.85-0.88), Lazecky 2021 (Procedia CS 181:1154-1161, S1+Radarsat-2, PCA), Reiche 2021 (ERL 16:054005, RADD Congo), Dalponte 2023 (RSE 297:113787, CSK+S1, RF), Balling 2024 (JAG 132:103994, PALSAR-2 ScanSAR+S1), Kuzu 2024 (IEEE JSTARS 17:4751-4767, S1+S2 SSL), Antoniadis 2025 (IJRS 46:1622-1651, ICEYE), van der Woude 2026 (RSE 337:115325, NRT-алерты Европа)
- Российский контекст (оптика): Shikhov 2020 (ESSD 12, Landsat), Kanev 2024 (ИЗК №3:3-15, Sentinel-2 U-Net), Korznikov 2025 (NHESS, Pleiades CNN)
- Вывод: в России ветровалы картируются оптикой; SAR-решение — исследовательская ниша

---
Task ID: 2
Agent: Super Z (main)
Task: Генерация деливераблов — Excel-таблица источников + Word-обзор

Work Log:
- Excel: scripts/build_xlsx_sources.py (xlsx skill, base.py, Calibri, B2-origin, автофильтр, freeze D5, 2 листа «SAR-работы» + «Сенсоры»); QA: recalc/audit/scan/validate — exit 0
- Word: scripts/gen_docx_helpers.js + gen_docx_content1/2.js + gen_docx_main.js (docx skill: обложка R1+FG-1, calcTitleLayout/calcCoverSpacing, 3 секции, римская/арабская нумерация, TOC)
- add_toc_placeholders.py --auto (exit 0, 13 заголовков); scripts/patch_docx_footers.py (PAGE \* ROMAN / \* arabic, удалён пустой pgNumType)
- postcheck.py: 8/9, 0 ошибок, 1 некритичное предупреждение (интервал в ячейках таблиц 276)
- Визуальная проверка PDF-рендера 14 страниц; исправлены: перенос лейбла на обложке, опечатка «penetrирует»→«проникает»

Stage Summary:
- /home/z/my-project/download/Обзор_детектирование_ветровалов_SAR.docx (14 стр.: обложка, аннотация, оглавление, 9 разделов, 2 таблицы, 16 источников по ГОСТ)
- /home/z/my-project/download/Источники_SAR_детектирование_ветровалов.xlsx (12 SAR-работ + справочник сенсоров, фильтры)

---
Task ID: 3
Agent: Super Z (main)
Task: Спецификация воркфлоу детектирования ветровалов по Sentinel-1 (доработка плагина QGIS: окна, предобработка, алгоритм, автоматизация)

Work Log:
- Извлечена и верифицирована полная методика Rüetschi 2019 из полнотекстового HTML (research/ruetschi2019_fulltext.txt, методы): окна (5 сцен ~6 нед до / до 10 сцен ≤4 нед после), RTC γ⁰ по Small 2011, LRW-композит, WI=ΔVV+ΔVH (рост γ⁰!), порог x̄(лес)+a, a≈3 дБ (2,8–3,35), n≈25 px, ЛМУ 0,5 га, PA 0,88/UA 0,85
- Повторно загружен навык docx; прочитана полная цепочка (create.md, docx-js-core.md, design-system.md, common-rules.md, toc.md, report.md)
- Переиспользованы хелперы gen_docx_helpers.js (R1/FG-1, ГОСТ-стиль проекта); patch_docx_footers.py параметризован argv
- Новые скрипты: spec_content1.js (аннотация, TOC, гл.1–3), spec_content2.js (гл.4–6, формулы через TextRun sub/superScript), spec_content3.js (гл.7–9 + 10 источников ГОСТ), gen_spec_main.js
- Исправлены опечатки смешанных алфавитов (мульти-tempоральный→мультитемпоральный, identified→выявленный, Superior→превосходящую, «слои известные»→«известных», «с вторым»→«со вторым»)
- Сборка: node gen_spec_main.js → add_toc_placeholders.py --auto (exit 0, 32 заголовка) → patch_docx_footers.py (roman/arabic) → postcheck 8/9, 0 ошибок, 1 некритичное предупреждение (интервал в ячейках таблиц)
- Визуальная проверка PDF-рендера (21 стр.): обложка R1/FG-1, TOC-плейсхолдеры, формулы γ⁰, 8 таблиц, источники — без дефектов

Stage Summary:
- /home/z/my-project/download/Воркфлоу_детектирование_ветровалов_Sentinel-1.docx (21 стр.: обложка, аннотация, оглавление, 9 глав, 8 таблиц, формулы WI/порога, 10 источников ГОСТ)
- Ответы на 4 вопроса воркфлоу: (1) окна t1 14–90 дн (5+ сцен), t2 ≤28 дн (до 10 сцен), сезон май–окт; (2) цепочка из 7 операторов SNAP до γ⁰_RTC+дБ, без спекл-фильтра; (3) WI=ΔVV+ΔVH, порог x̄+3 дБ, фильтры n=25 px и 0,5 га; (4) Processing-провайдер + gpt-графы + пресеты JSON + режим мониторинга
- Дорожная карта плагина: MVP→v0.9→v1.0→v1.1 с критериями приёмки
---
Task ID: 3
Agent: main (Super Z)
Task: Рефакторинг плагина sentinel1_plugin v0.6.0 (поиск/скачивание/препроцессинг + нефть/паводок/RGB) в плагин детектирования ветровалов Sentinel-1 (v0.7.0): убрать лишний функционал, добавить детекцию WI, проверить код, скомпилировать ZIP; подготовить план тестирования на реальных данных (geocartography.ru 2020_4_19-30 → БД Шихова ESSD 2020).

Work Log:
- Распаковал upload/sentinel1_plugin_project_v0.6.0.zip, изучил структуру (диалог 2580 строк, sources/, tests/, 4 таба)
- Прочитал статью по ссылке: Шихов, Абдуллин, Семакина (2020) Геодезия и картография №4:19-30 — картографирование подверженности лесов Урала ветровалам; источник эталона — БД Shikhov et al. 2020 ESSD 12:3489-3513 (figshare 10.6084/m9.figshare.12073278: 700 событий, даты+полигоны, 1986-2017)
- ВАЖНО: уточнил метод по полному тексту Rüetschi et al. 2019 — ветровал = РОСТ обратного рассеяния, WI = (VV_post−VV_pre)+(VH_post−VH_pre) ПОЛОЖИТЕЛЕН; порог = mean(WI)+a (a≈2.9 дБ опт.), n=27 px; композиты 5 сцен до / ≤10 после (~2-4 нед)
- Создал дерево v0.7.0: sentinel1_windthrow_plugin; удалил OilSpill/Flood/RGB/PolarizationComposite (analyzers.py, test_analyzers.py), старые release notes, spec/process/instructions/review, встроенный zip v0.5.0
- Написал sources/windthrow.py: pair_by_polarization, to_db_domain_chunk, compute_wi, adaptive_threshold, mask_from_threshold, filter_small_objects (scipy, 8-связность), median_filter_nan, ensure_aligned (gdal.Warp), build_median_composite (чанками 512 строк), rasterize_vector_mask, WindthrowDetector.detect_file (чанковый WI → адаптивный/фикс. порог → медианный фильтр → очистка объектов → маска → gdal.Polygonize 8-connected → фильтр по площади → GPKG/SHP с area_ha), диагност. mean WI/threshold/n_objects, отмена/прогресс
- Переписал диалог (скриптом scripts/splice_dialog.py): таб 3 «Windthrow Detection» (стеки до/после, режимы порога, медианный фильтр, min px, маска-ограничитель, output base → _wi.tif/_mask.tif/.gpkg), Settings без нефть/паводок (+WI offset, min px), подсказки поиска под ветровал (RTC рекомендован, одна орбита, VV+VH), About v0.7.0, _add_vector_to_map
- Обновил metadata.txt (name=Sentinel-1 Windthrow Detector, 0.7.0, теги), sentinel1_plugin.py (меню/панель), sources/__init__.py (15 экспортов), conftest/импорты тестов под новое имя пакета
- Написал tests/test_windthrow.py (13 тестов): знаковая физика WI, порог/маска, объектный фильтр, медианный фильтр с NaN, спарсинг поляризаций + 4 сквозных GDAL-теста на синтетических сценах (блоб +4 дБ в post, круг r=10 px): пайплайн, мультитемпоральный композит, GPKG area_ha≈3.14 га, векторная маска
- Проверки: py_compile всех файлов OK; pytest — 72 passed (Python 3.12 без GDAL: 67 pass/5 skip; системный Python 3.13 + GDAL 3.10.3: 72 pass). Поймал и исправил реальный баг: ogr Geometry.GetArea() (не Get_Area)
- Документы: README.md (переписан), METHOD.md (метод+параметры+ссылки), TESTING_PLAN.md (RU: T0-T3, критерии выбора события 2016-2017, окна, PA/UA object-based, сетка a/n, реестр источников ложных тревог), RELEASE_NOTES_v0.7.0.md
- Скомпилировал и упаковал /home/z/my-project/download/sentinel1_windthrow_plugin_v0.7.0.zip (29 файлов, чистый корень пакета)

Stage Summary:
- download/sentinel1_windthrow_plugin_v0.7.0.zip — готов к установке в QGIS (Install from ZIP), 72/72 тестов
- Детекция: WI=ΔVV+ΔVH (положительный = ветровал), адаптивный порог mean+a (a=2.9 дБ, Rüetschi 2019), n=27 px, медианный фильтр 3×3, маска леса опционально, векторизация с area_ha
- Тестирование на реальных данных: БД Shikhov et al. 2020 ESSD (figshare 12073278) — события 2016-2017 в эпоху S1; окно до −21..−1 дн, после +1..+14 дн, одна орбита, RTC для Урала; метрики PA/UA object-based, сетка a/n как в статье
- Исходники: /home/z/my-project/plugin_work/sentinel1_windthrow_plugin/ (исходное дерево), скрипт сплайса диалога в scripts/splice_dialog.py

---
Task ID: 4 (handoff — статус на конец дня, план на завтра)
Agent: main (Super Z)
Task: Фиксация состояния проекта и плана следующей сессии (тестирование на реальных данных)

Work Log:
- Проверено наличие всех артефактов в download/ (docx-обзор, docx-воркфлоу, xlsx-источники, zip плагина v0.7.0) и исходного дерева в plugin_work/
- Составлен план сессии «завтра» (см. Stage Summary)

Stage Summary — ЧТО ГОТОВО (не трогаем):
1. download/Обзор_детектирование_ветровалов_SAR.docx — литобзор 14 стр., 16 источников ГОСТ Р 7.0.100-2018
2. download/Воркфлоу_детектирование_ветровалов_Sentinel-1.docx — спецификация 21 стр.: окна t1 14–90 дн (5+ сцен) / t2 ≤28 дн, цепочка SNAP до γ⁰_RTC дБ, WI=ΔVV+ΔVH (рост = ветровал), порог mean+a, дорожная карта MVP→v1.1
3. download/Источники_SAR_детектирование_ветровалов.xlsx — 12 SAR-работ + справочник сенсоров
4. download/sentinel1_windthrow_plugin_v0.7.0.zip — плагин QGIS: убраны нефть/паводок/RGB, добавлен таб Windthrow Detection (детектор sources/windthrow.py: WI → порог → медианный фильтр → очистка → векторизация с area_ha), 72/72 pytest pass, py_compile OK
5. Доки плагина: README.md, METHOD.md, TESTING_PLAN.md (T0–T3), RELEASE_NOTES_v0.7.0.md — в plugin_work/sentinel1_windthrow_plugin/

Stage Summary — ПЛАН НА ЗАВТРА (тестирование на реальных данных, по методике Геодезия и картография 2020 №4:19-30):
- T0. Скачать эталонную БД Shikhov et al. 2020 ESSD (figshare 10.6084/m9.figshare.12073278, ~700 событий 1986–2017), отфильтровать события 2016–2017 в зоне покрытия Sentinel-1 (S1A работает с конца 2014; EW/IW GRD)
- T1. Выбрать 1–2 тестовых шторма (критерии в TESTING_PLAN.md: крупный полигон ≥50 га, даты внутри окна ±2 нед, одна орбита). Кандидаты по статье — штормы на Урале 2016–2017
- T2. Через плагин: поиск пары «до/после» (окно до −21..−1 дн, после +1..+14 дн, VV+VH, одна орбита), скачивание, препроцессинг до γ⁰_RTC (для Урала важна террейн-коррекция — рельеф!)
- T3. Прогон Windthrow Detection, сетка параметров a∈{2.5, 2.9, 3.3} дБ × n∈{20, 27, 50} px, расчёт PA/UA object-based против полигонов Shikhov, реестр ложных тревог (сельхозполя, вырубки, водоёмы)
- Открытые вопросы для обсуждения с пользователем: доступность Copernicus Data Space (нужна ли регистрация в плагине), размер AOI, есть ли своя маска леса (GWL/LC) для фильтра-ограничителя
- Ключевые файлы следующей сессии: plugin_work/sentinel1_windthrow_plugin/TESTING_PLAN.md, research/essd_shikhov_2020.json, research/geocartography_2020_4_19-30.json

---
Task ID: 5
Agent: main (Super Z)
Task: Анализ загруженного пользователем плагина dem_comparator-0.1.0.zip (источник ЦМР); уточнение роли Copernicus Data Space и ЦМР в воркфлоу ветровала

Work Log:
- Разъяснил пользователю: Copernicus Data Space упоминался только как резервный источник снимков S1 (регистрация не обязательна — плагин качает через Planetary Computer анонимно); ЦМР для теста отдельно не нужна — продукт RTC (sentinel-1-rtc) уже содержит террейн-коррекцию по Copernicus DEM GLO-30, выполненную серверно
- Распаковал upload/dem_comparator-0.1.0.zip в plugin_work/dem_comparator_extract/dem_comparator/; изучил архитектуру (~2070 строк)
- Подтвердил преемственность: core/pc_client.py (254 стр.) dem_comparator — прямой предок расширенного sources/pc_client.py (405 стр.) плагина ветровала (добавлены пагинация, ретраи 429/5xx, универсальный sign-эндпоинт, кэш токенов)
- Структура: core/ (pc_client, pc_mosaic — VRT-мозаика + gdal.Warp без материализации тайлов, extent_utils — общий грид EPSG:4326, align, compare, report — HTML-отчёт, downloader_base, orchestrator, task) + sources/ (5 ЦМР через PCTileSpec: glo30 → cop-dem-glo-30/data/30м глобально; fabdem → fabdem/data/30м DTM без леса и зданий; nasadem; aw3d30 → alos-dem; srtm → srtm-gl-1/elevation/30м, только ±60°) + ui/ (main_dialog, draw_rectangle_tool — общий с S1-плагином предок)
- Оценены сценарии использования для ветровала: (1) тест завтра — ЦМР не нужна, RTC-продукт; (2) если гнать сырой GRD через SNAP — GLO-30 можно скачать этим плагином и подсунуть в оператор Range-Doppler; (3) v1.1 — склон-фильтр для подавления ложных тревог: скачать GLO-30 на AOI, рассчитать уклон, замаскировать >X°; (4) порт слоя sources/ (BaseDEMSource+PCTileSpec+glo30+pc_mosaic) в плагин ветровала для кнопки «Скачать ЦМР» — архитектуры совместимы, база — наш расширенный pc_client

Stage Summary:
- dem_comparator — референс-плагин, из которого вырос клиент PC плагина ветровала; для завтрашнего теста интеграция не требуется
- Плагин полезен как: standalone-источник GLO-30 для SNAP-обработки GRD; кандидаты порта для v1.1 (склон-фильтр). FABDEM (DTM без леса/зданий) для детекции ветровала напрямую не нужен
- Решение отложено до обсуждения: port sources-слоя ЦМР в v1.1 или оставить standalone
- Открытый вопрос «доступность Copernicus Data Space» из Task 4 снят: для теста 2016–2017 Planetary Computer достаточен, регистрация не нужна

---
Task ID: 6
Agent: main (Super Z)
Task: Верификация временного перекрытия БД Shikhov 2020 ESSD и эпохи Sentinel-1 (вопрос пользователя: «база до какого года? S1 запустили позже»)

Work Log:
- Скачал полный датасет figshare 12073278 v6 (ER_windthrow_dataset.zip, 68.5 MB) → research/shikhov_db/; распакованы CSV и GIS-слои
- Схема слоя Windthrows.shp (700 полигонов): ID, Storm_ID, Storm_type, Certainty, Year, Month, Date_1/Date_2 (диапазон дат по Landsat-ревизитам), N_polygons, Area (km²), Length, Mean_width, Wind_gust, Direction
- Покрытие базы: 1986-01-01 … 2017-09-19 (метаданные), 700 ветровалов / 486 штормов / 102747 элементарных полигонов, европейская Россия (52.4-66.6 N, 28.1-61.1 E)
- Подтверждено опасение пользователя: S1A (запуск 03.04.2014, штатные IW GRD с октября 2014) покрывает лишь хвост базы. НО: событий эпохи S1 (2015-2017) — 127 ветровалов на 200.4 km² (2015: 28, 2016: 44, 2017: 55); типы: 77 торнадо, 44 шквала, 6 снежных штормов; достоверность: 93 High
- Отфильтрованы кандидаты: 59 событий с известной датой, площадь ≥0.5 km² (50 га), Certainty High/Medium → research/shikhov_db/s1_era_candidates.json
- Топ-кандидаты (крупные, летние, узкое окно дат): ID655/S471 2017-07-20..08-02 18.2 km² squall; ID578/S415 2015-06-23..07-01 11.2 km² squall; ID666/S466 2017-07-29..08-02 9.5 km² squall (окно 4 дня); ID583/S423 2015-07-25..08-05 8.4 km²; ID674/S482 2017-07-31..08-16 6.6 km² tornado
- Забракованы: события снежных штормов окт 2015 (ID590, 31.7 km²) — диапазон дат растянут на месяцы, листопад; события 1986-2014 — до S1
- Наблюдение для статьи: ревизит S1 6/12 дней позволяет сузить даты событий, оставленные в БД как диапазоны Landsat (16 дней) — побочная научная ценность теста
- Скрипты: scripts/analyze_shikhov_db.py, scripts/s1_era_candidates.py

Stage Summary:
- Перекрытие БД и S1: октябрь 2014 – сентябрь 2017; в окне 127 событий (200 km²) — материала для T1-T3 достаточно
- План T1 уточнён: выбор из 59 кандидатов (критерии TESTING_PLAN.md), приоритет — летние шквалы 2015/2017 с узким окном дат и площадью ≥5 km²
- Артефакты: research/shikhov_db/ER_windthrow_dataset.zip, s1_era_candidates.json, storms_summary.json; рабочий вывод — 2016–2017 из TESTING_PLAN.md расширено до 2015–2017

---
Task ID: 7 (handoff — фиксация состояния, закрытие сессии)
Agent: main (Super Z)
Task: Финальная фиксация состояния проекта по запросу пользователя («фиксируем состояние дел и до завтра»)

Work Log:
- Выгружен человекочитаемый ворклог: download/Ворклог_детектирование_ветровалов_плагин.md (сводная таблица деливераблов, Task 1–6, таблица топ-5 кандидатов, план следующего шага)
- Обновлён план сессии «завтра» с учётом результатов Tasks 5-6 (замена версии из Task 4)

Stage Summary — ФИНАЛЬНОЕ СОСТОЯНИЕ НА КОНЕЦ СЕССИИ:
- Деливераблы в download/: Обзор_SAR.docx (14 стр.), Воркфлоу_Sentinel-1.docx (21 стр.), Источники.xlsx, sentinel1_windthrow_plugin_v0.7.0.zip (72/72 тестов), Ворклог_...плагин.md
- База эталона скачана и проанализирована: research/shikhov_db/ (Windthrows.shp 700 полигонов, 1986-2017); в эпоху S1 — 127 событий/200 км², 59 кандидатов в s1_era_candidates.json
- ЦМР: не нужна для теста (RTC-продукт PC); dem_comparator — донор слоя sources/ для v1.1 (склон-фильтр GLO-30); Copernicus Data Space не требуется

ПЛАН НА ЗАВТРА (T1-T2, затем T3):
1. T1: выбор шторма — фаворит ID666/S466 (2017-07-29..08-02, 9.5 km², окно 4 дня), запасные ID655 (18.2 km²) и ID578 (11.2 km²); координаты AOI взять из геометрии слоя Windthrows.shp по ID
2. T2: в плагине — поиск пары «до/после» (до −21..−1 дн, после +1..+14 дн, VV+VH, одна орбита, продукт RTC), скачивание в AOI
3. Прогон Windthrow Detection: режим адаптивного порога, медианный фильтр 3×3, min px 27; выход _wi.tif/_mask.tif/.gpkg
4. T3: сетка a∈{2.5,2.9,3.3}×n∈{20,27,50}, PA/UA object-based по полигонам ID, реестр ложных тревог
5. Если замедление/ошибка скачивания PC — вернуться к вопросу резервного источника (Copernicus Data Space)
- Ключевые файлы: TESTING_PLAN.md, s1_era_candidates.json, research/geocartography_2020_4_19-30.json, download/Ворклог_детектирование_ветровалов_плагин.md

---
Task ID: 5
Agent: main (Super Z)
Task: Фиксация полного контекста проекта в подробный ворклог после утери результатов сессии 01.09 (панель загрузки у пользователя снова не работает)

Work Log:
- Проверено состояние песочницы после перезапуска: download/ (4 деливерабла + ZIP v0.7.0), plugin_work/sentinel1_windthrow_plugin (v0.7.0, кода v0.8 НЕТ — normalize_background отсутствует), research/shikhov_db (БД Шихова цела: Windthrows.shp, Storm_events.shp, CSV, s1_era_candidates.json с ID666), tool-results/ (только следы 30.08)
- Подтверждены потери сессии 01.09: код normalize_background, run_windthrow_test_666_step7.py, warp-кэш 9 сцен (2,7 ГБ), бейслайны П.1, CONTEXT-док
- Проверено окружение: /usr/bin/python3 = 3.13.5 + GDAL 3.10.3 + pip 25.1.1, pytest ОТСУТСТВУЕТ; диск 8,9 ГБ свободно
- Составлен и записан полный контекст-ворклог: download/ВОРКЛОГ_контекст_2026-09-02.md (11 разделов: суть, инвентарь, потери, хронология, решения штурма 1б/2б/3б/4/5/6, спецификация v0.8 normalize_background с API и тестами, техконстанты, план на завтра 0-5, критерии интерпретации A-D, дисциплина фиксации, риски)
- Полный текст ворклога продублирован в чат (панель загрузки не работает)

Stage Summary:
- download/ВОРКЛОГ_контекст_2026-09-02.md — единый источник контекста для сессии-продолжения
- Следующий шаг сессии: Шаг 0 (pip install pytest, 72/72) → Шаг 1 (v0.8 в плагин: normalize_background + 4 теста + GUI-чекбокс + ZIP v0.8.0) → Шаги 2-5 (step7 warp/detect/report, 4 комбинации {пара,стек}x{норм вкл/выкл}, JSON бейслайнов)

---
Task ID: 6 (Шаг 1)
Agent: main (Super Z)
Task: Реимплементация v0.8 normalize_background в плагин + тесты + GUI + ZIP v0.8.0

Work Log:
- Восстановлено окружение: pytest 9.1.1 (--break-system-packages), scipy 1.18.1; 72/72 тестов v0.7.0 подтверждены
- sources/windthrow.py: параметр normalize_background=True в __init__; detect_file(+background_mask_path); хелпер _resolve_mask_raster; новая чистая функция background_offset_db (медиана post-pre, stride-сабсэмплинг до 8M); проход 2b вычисления offset по поляризациям; вычитание offset в WI-проходе; имя *_wi_norm.tif при норме; статистика x̄ по bg-маске (fallback: analysis mask → вся сцена); диагностика offset_db в отчёте
- Дизайн-решение: маска фона (offset + x̄) отделена от analysis mask (ограничение детекции) — иначе PA обнулился бы; фон = буфер эталона минус полигоны (решение 4), детекция — по всей AOI
- GUI: чекбокс "Background normalization (remove weather shift)" (по умолчанию ВКЛ) в _build_windthrow_tab + проводка в _on_windthrow_run; подсказка выхода обновлена
- Тесты +4: unit background_offset_db; сквозной со сдвигом +1.5 дБ (offset≈1.5, mean_wi≈0, блоб+8дБ детектируется); регрессия norm=False (_wi.tif, offset_db={}, mean_wi≈+3.0); bg-маска исключает блоб из статистики но не из детекции
- metadata.txt 0.8.0; RELEASE_NOTES_v0.8.0.md; README/METHOD обновлены; sources/__init__.py +экспорт
- pytest: 76 passed; ZIP download/sentinel1_windthrow_plugin_v0.8.0.zip (29 файлов)
- Фиксация: JSON download/status_task_v08_plugin_2026-09-02.json; git commit d6df5df; чекпоинт download/checkpoint_20260902_task1_v08.tar.gz

Stage Summary:
- Плагин v0.8.0 готов: normalize_background работает, 76/76 тестов, ZIP в download/
- Ключевой API для step7: WindthrowDetector(threshold_mode, a_db, min_pixels=27, median_filter_size=3, normalize_background).detect_file(pre, post, base, analysis_mask_path, background_mask_path=bg)
- Следующий шаг: step7-скрипт (warp/detect/report) + STAC-верификация сцен

---
Task ID: 7 (Шаги 2-5)
Agent: main (Super Z)
Task: step7 — полный эксперимент на ID666 (warp 6 сцен, детекция 6 вариантов, метрики, анализ)

Work Log:
- step7-скрипт scripts/run_windthrow_test_666_step7.py (стадии warp/detect/report, resume по JSON/файлам)
- Пойманы и исправлены 4 бага: (1) оси EPSG:4326 (SetAxisMappingStrategy OAMS_TRADITIONAL_GIS_ORDER) — событие «уезжало» в Узбекистан; (2) vsicurl-загрузка полосы целиком 890+ МБ → параллельный оконный копир COG (4 потока, 4.2x, 55 с/полоса); (3) object_metrics O(n_obj × 26.8M px) вис часы → векторизация bincount; (4) сверка origin дат — ко-регистрация OK (379250, 6887140 у всех)
- Маски: ref 950.0 га (совпадение с БД 1:1), bg-кольцо 3 км 56404 га; сетка 5245x5108 px EPSG:32638
- Фактические орбиты (STAC): rel orbit 94, abs 6070(16.06)/6245(28.06)/6420(10.07)/6595(22.07)/6770(03.08)/6945(15.08) — в старом ворклоге BASE ошибочно была 6420=22.07
- 6 вариантов: A пара-мокрая adaptive (thr 5.66, 9506 га), B пара+norm+fixed3.0 (8648), C стек adaptive (7335), D стек+norm+fixed (5740), E пара-сухая 15.08 adaptive (9238), F стек-сухой (7131); PA 0.08-0.14, UA 0.015-0.023 везде
- ГЛАВНЫЕ ВЫВОДЫ: (1) мокрый сдвиг подтверждён (+0.9..1.2 дБ/пол, 10.07 тоже мокрая); (2) adaptive-порог МАТЕМАТИЧЕСКИ ИНВАРИНТЕН к аддитивной нормализации (B_adaptive≡A пиксель-в-пиксель — негативный результат, norm полезен только с fixed-порогом); (3) СИГНАЛА ВЕТРОВАЛА В ЛИСТ-ОН C-ДИАПАЗОНЕ НЕТ: WI медиана в эталоне на ~1 дБ НИЖЕ лесного фона (фон леса +3..4 дБ от влаги+фенологии, повреждённый древостой меньше); (4) ложные тревоги = поля/вырубки (лесная маска v0.9 обязательна); (5) стек снижает площадь срабатываний на 20-25%
- Артефакты: download/windthrow_id666_{A..F}.json, baselines_2026-09-02.json, analysis_2026-09-02.json, final_2026-09-02.json, step7_warp_manifest.json, 2 чекпоинт-архива
- Git: 82b30e3, 4878649; ZIP v0.8.0, 76/76 тестов (Task 6)

Stage Summary:
- Пайплайн работает end-to-end и воспроизводим; для детекции ID666 лист-он нужны лесная маска + смена индекса/сезона (лист-офф события БД)
- Рекомендация: v0.9 = лесная маска + документирование инвариантности; экспериментальная ветка = лист-офф события из 59 кандидатов

---
Task ID: 8
Agent: main (Super Z)
Task: Облачная доставка артефактов (панель загрузки у пользователя снова не работает; запрос «положи плагин и ворклог на облако»)

Work Log:
- Верифицировано состояние перед отгрузкой: pytest 76/76 (3.0 c), ZIP v0.8.0 цел (unzip -t, No errors), git-история до 4878649 (step7 complete), все JSON/MD/чекпоинты в download/ на месте
- git-bundle отклонён: .git = 2.3 ГБ (в историю когда-то попали бинарные данные) — вместо него в бандлы включены 3 чекпоинт-архива (в сумме ~0.4 МБ)
- scripts/make_cloud_bundles.py → download/cloud/: windthrow_worklogs_20260902.zip (4 md), windthrow_id666_results_20260902.zip (13 JSON), windthrow_project_full_20260902.zip (25 файлов, 744 КБ), + копия worklog.md как windthrow_worklog_current_20260902.md
- scripts/upload_cloud.sh: catbox.moe отклонил («Invalid uploader» — блок датацентровых IP), pixeldrain требует API-ключ (authentication_required) → все 5 файлов приняты litterbox.catbox.moe (хранение 72 ч)
- Целостность подтверждена обратным скачиванием: md5 всех 5 ссылок совпал с локальным

Stage Summary:
- ССЫЛКИ ОБЛАКА (действуют до ~2026-09-05, далее перезаливать по запросу):
  - Плагин v0.8.0 (QGIS Install from ZIP): https://litter.catbox.moe/t8tonp.zip  md5 0a74ff9afae754809f925bee7852ec1f
  - Ворклоги (3 шт + README): https://litter.catbox.moe/zzxskp.zip  md5 494d58c2daf1ddd96678bbcdd404ea93
  - Результаты step7 (13 JSON): https://litter.catbox.moe/dzhr19.zip  md5 7bfec8fb48e66633ec5230e45de7302d
  - ПОЛНЫЙ бандл (25 файлов: плагины 0.7+0.8, доки, JSON, ворклоги, чекпоинты): https://litter.catbox.moe/uvgj0x.zip  md5 6cb81739b0a6fb63e15b1dfee42216e0
  - Текущий worklog.md (raw): https://litter.catbox.moe/2763hy.md  md5 11b201ca4167e5ef5bdcd04638c6890d
- Бандлы продублированы локально в download/cloud/; скрипты перезалива сохранены в scripts/
- Ограничения: ссылки публичные (файлы без персональных данных); litterbox 72 ч — пользователю скачать сразу; для постоянного хранения — GitHub-репо по токену пользователя (опция)

---
Task ID: 9 (step8 — лист-офф)
Agent: main (Super Z)
Task: Прогон детекции по осенним и зимним ветровалам БД Шихова (запрос пользователя «давай сначала прогоним по осенним и зимним ветровалам»)

Work Log:
- Отбор кандидатов: зимних событий в БД НЕТ (под снегом Landsat не картографирует — слепая зона эталона); осенних два кластера: снежный шторм S425 08.10.2015 (ID590 31,7 км²/1062 полигонов + ID589/587/588/591/592 вдоль 59° в.д.) и торнадо S486 ~19.09.2017 (ID694, 1,61 км², Пермский край)
- scripts/run_windthrow_leafoff_step8.py — обобщение step7: стадии recon/warp/detect/report, per-event конфиг (epsg, окна, min_pre), автораскладка цикла (base = ближайшая pre), мозаика сцен одного дня (рамка RTC), растеризация эталона через memory-слой с перепроектированием + EDT-кольцо 3 км
- Пойманы и исправлены 6 багов: (1) OOM на последовательном Union 1062 частей (UnionCascaded тоже) → полный отказ от GEOS-union: растеризация + EDT; (2) RasterizeLayer не перепроецирует 4326→UTM (ref 0 га); (3) чтение маски вторым хэндлом до закрытия пишущего → 0 га (161,2 га через тот же handle — 1:1 с БД); (4) warp-фолбэк без dstSRS → пусто; (5) _epsg_of_proj → ложный crs_match=False (IsSame); (6) zip-раскладка меток обрезала base при <4 pre → right-align
- ID590: НЕВОЗМОЖНО на PC RTC — за 21.08–06.11.2015 найдено 2 срезa (один пролёт 21.09.2015, rel 35); у RTC нет покрытия Урала-2015; нужен GRD+SNAP тракт
- ID694 (S1B desc rel 152: pre 07/19/31.08, post 24.09 и 06.10.2017): контраст WI ref−bg = +0,024/+0,021 дБ (знак перевернулся против лист-она ID666, но амплитуда ~0); PA 0,059–0,059/UA 0,016–0,029; стектоп C/D PA=0
- 6660 (новый эксперимент — годовой лист-офф дифференциал ID666: base 07.10.2016 → post 02.10.2017/post2 14.10.2017, rel 94; ref 950,1 га 1:1): контраст +0,012/+0,017 дБ; октябрьский межгодовой сдвиг погоды +0,71…0,77 дБ/поляр (offset), mean_wi 1,5–2,5 дБ → адаптивный порог 4,4–5,4 дБ; PA 0,086–0,116 / UA 0,008; фиксированный+norm — 22–27 тыс. га ложных (не-лес)
- ГЛАВНЫЙ ВЫВОД (тройной негатив): WI на C-диапазоне S1 RTC 10 м не детектирует уральские ветровалы НИ в лист-он (−1,0 дБ), НИ в лист-офф (+0,01…0,02 дБ против порога 2,9 дБ); метод Рюэчи (Альпы, PA 0,85–0,88) не переносится на смешанные леса Урала — кандидат: L-band (PALSAR-2/ROSE-L), X-band высокая детализация, редизайн индекса (dVH, VH/VV, log-ratio), лесная маска обязательна
- Артефакты: download/step8_id{694,6660}_warp_manifest.json, windthrow_id694_{A..F}.json, windthrow_id6660_{A,B,E}.json, windthrow_id{694,6660}_leafoff_baselines_2026-09-02.json, windthrow_leafoff_analysis_2026-09-02.json; git 3dbbaa5; чекпоинт download/checkpoint_20260902_step8.tar.gz

Stage Summary:
- Дифференциальная диагностика сезонности завершена: C-сигнал отсутствует в оба сезона; направления дальнейшей работы — v0.9 лесная маска + индекс-студия (dVH/VH/VV/log-ratio на кэше) + L-band зондаж; ID590 — только через GRD+SNAP

---
Task ID: 10
Agent: main (Super Z)
Task: Подключение GitHub (запрос пользователя «а может нам правда подсоединиться к githab?»)

Work Log:
- Проверена связность из песочницы: github.com HTTP 200, git-over-HTTPS работает (ls-remote октоката OK); api.github.com 403 только анонимно (рейт-лимит общей IP 8.212.10.159) — с PAT авторизованные запросы проходят
- Старый .git = 3,0 ГБ (бинарные данные в истории) на GitHub не переедет → решение: свежая чистая история, сборка репо заново (чекпоинты и ворклог сохраняют преемственность)
- scripts/prepare_github_repo.sh → github_repo/ (6,1 МБ, 79 файлов): qgis_plugin/ (исходники v0.8.0 — 76/76 тестов проходят в копии), plugin_dist/ (ZIP 0.7.0+0.8.0 для Install from ZIP), pipeline/ (+ diag/), results/ (все JSON step7/step8 + сводки shikhov_db), data/shikhov_csv/ (открытый ESSD CSV, 5 МБ), docs/ (docx/xlsx + ворклоги), worklog.md, README.md (метод, таблица результатов step7/step8, roadmap, атрибуция), .gitignore (tif/gpkg/pycache)
- git init -b main; локальный identity «Sentinel-1 Windthrow Team <sentinel1.sar.plugin@example.com>»; commit 40cd132
- scripts/push_to_github.sh: GITHUB_TOKEN (+опц. GITHUB_USER/REPO_NAME/PRIVATE=true) → POST /user/repos (201 создано / 422 уже есть / 000 — фолбэк на пуш без API) → push origin main → снятие токена из remote URL

Stage Summary:
- Репо собрано и готово к пушу: ожидается классический PAT (scope repo) от пользователя; main, 1 коммит, 6,1 МБ — уложится в лимиты GitHub с запасом
- Шейпы БД Шихова (128 МБ) в репо не включены — только CSV и производные сводки
- После первого пуша: v0.9 (лесная маска) и студия индексов продолжаются прямо в репо; litterbox-ссылки (Task 8) остаются как одноразовая доставка

Task 10 — завершение (push выполнен):
- Токен пользователя валиден (login nadiopt-cell); репо nadiopt-cell/sar_windthrow создано пользователем заранее (private, пустое) → POST вернул 422, скрипт перешёл к пушу
- git push -u origin main: OK, * [new branch] main -> main; верификация: remote HEAD = local HEAD = 40cd132c0b04fabd3738ba5f9d63d24d84a19ccc; API git/trees: 79 blob'ов, все 9 top-level секций на месте
- Токен снят из remote config (origin = чистый https URL); в выводе пуша токен маскировался sed'ом
- Артефакт: https://github.com/nadiopt-cell/sar_windthrow (приватный, 6,1 МБ, 1 коммит)
- Гигиена: токен ghp_... попал в чат — рекомендовать пользователю revoke после окончания совместных пушей или ротацию на fine-grained

---
Task ID: 11 (v0.9 — лесная маска + step9)
Agent: main (Super Z)
Task: Реимплементация лесной маски в плагин (v0.9.0) + валидация на ID666 (запрос «поехали по порядку — идея №1»)

Work Log:
- sources/forest_mask.py: ESA WorldCover через PC STAC (collection esa-worldcover, asset map, класс 10 Tree cover) — search/sign/vsicurl-warp на сетку S1 (nearest) → бинаризация → majority 3×3; dispatcher build_forest_mask(worldcover|file); bbox_4326 через osr с OAMS_TRADITIONAL_GIS_ORDER (урок step7); read_ref_info — публичная обёртка
- windthrow.py: detect_file(+forest_mask_path) — пересечение с analysis mask (_intersect_masks, чанками); при отсутствии bg-mask статистика (offsets+mean) идёт по лесу (поведение статьи); в result добавлен forest_mask
- GUI: чекбокс «Restrict detection to forest mask» + источник (Custom file | ESA WorldCover auto) + год 2020/2021; авто-скачивание маски в _work до детекции (прогресс 0-20% маска, 20-100% детекция); маска добавляется на карту, выводится в сообщении
- Баг пойманный тестами: numpy-фолбэк majority-фильтра суммировал значения (0/255) вместо количества соседей — исправлен (голосование по бинарной маске)
- Тесты 76→90 (+14): classify/majority (scipy и фолбэк), warp на сетку, bbox_4326, STAC-стабы, детектор: ограничение, пересечение mask∩forest, статистика по лесу, обратная совместимость
- step9 (scripts/run_windthrow_forestmask_step9.py, маски/detect/report, resume): WC-2020 собран за 7 с; лес 81,5% AOI, покрытие эталона 98,1% (wc_closed 83,6%/100%); VH-прокси −18…−19 дБ — почти no-op (97-98% AOI «лес», медиана VH −13,9 дБ — летняя тайга)
- Детекция 3 варианта (A/C/D) × 3 режима маски, пороги идентичны step7 (статистика на bg-кольце): PA не изменилась (0.126/0.111/0.084), UA +13-15% отн. (A 0.015→0.017, C 0.020→0.023, D 0.023→0.026), ложно-тревожная площадь −19…−40% (C 7335→4744 га, D 5740→3427 га); остаточные FP — мелкие объекты внутри леса (вырубки/депрессии) → следующий рычаг: min_pixels/индекс
- Артефакты: download/step9_forest_mask_diag_2026-09-02.json, windthrow_id666_{A,C,D}_{wc,wc_closed,vh185}_step9.json (9 шт), windthrow_id666_forestmask_step9_2026-09-02.json; ZIP download/sentinel1_windthrow_plugin_v0.9.0.zip (32 файла, unzip -t OK)
- Доки: metadata 0.9.0, RELEASE_NOTES_v0.9.0.md, README (бейджи 0.9.0/90 tests), METHOD §3b (лесная маска + caveat про регенерацию)

Stage Summary:
- v0.9.0 готов: лесная маска WorldCover работает end-to-end в плагине (STAC→маска→детекция), 90/90 тестов
- step9: маска безвредна для PA и умеренно полезна для UA; главный остаточный шум — внутри леса; WorldCover рекомендован вместо VH-прокси
- Дальше: студия индексов (dVH/VH/VV/log-ratio) и/или увеличение min_pixels как следующий шаг против лесных FP
