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

---
Task ID: 12 (step10 — студия индексов)
Agent: main (Super Z)
Task: Перебор SAR-наблюдаемых (dVV/dVH/WI/Δ(VH−VV)/веса) на кэше ID666+ID694 — есть ли вообще разделимость в C-диапазоне

Work Log:
- scripts/run_index_studio_step10.py: стадии study (чанковый сэмплер, 8 индексов = линейные комбинации dVV,dVH) и detect (in-memory object-level, не понадобился)
- Фон = bg-кольцо ∩ WC-лес (ID666) / кольцо (ID694); метрики: контраст медиан, ROC AUC (Mann-Whitney, без sklearn), порог Юдена (оракул), адаптивный x̄+2.9 (рабочий режим)
- РЕЗУЛЬТАТ (пиксельный уровень): 666_stack: все 8 индексов контраст −0.13…−0.02 дБ, AUC 0.487–0.495 (НИЖЕ случайности); 666_pair: контраст ≤+0.02 дБ, AUC ≤0.539; 694 leaf-off: лучший WI_vh1.5 AUC 0.577, WI 0.577, dpol — анти-сигнал (AUC 0.436)
- Адаптивный порог x̄+2.9 дБ: TPR=0.000-0.132 на всех событиях/индексах — рабочая зона порога статьи недостижима
- ВЫВОДЫ: (1) НИКАКАЯ линейная комбинация dVV/dVH не спасает C-диапазон на уральских событиях (для PA 0.85+ нужен AUC ≥0.8, есть ≤0.58); (2) сумма WI = уже оптимум внутри линейного семейства (выбор статьи подтверждён исчерпывающе); (3) Δ(VH−VV) ≡ Δlog-ratio — вреден; (4) веса ±0.01 AUC — шум
- Направление: текстура/пространственная неоднородность, интерферометрическая когерентность (SLC), временные ряды, L-band; одиночная пара C-band RTC мощности — исчерпана
- detect-стадия не запускалась: AUC≈0.5 делает object-level прогоны избыточными (step7/8 уже показали PA 0.08-0.13)
- Артефакты: download/windthrow_index_studio_step10_2026-09-02.json; залив №1 680dae3 (step9-скрипт+delivery), залив №2 — step10

Stage Summary:
- Индекс-студия закрыла вопрос «может, другой индекс?»: нет — линейная семья C-band исчерпана, WI оптимален
- Плагин v0.9.0 остаётся финальной функциональностью; следующий содержательный шаг — когерентность (SLC) или L-band, вне рамок текущего трека

---
Task ID: 18
Agent: main (Super Z)
Task: Восстановление песочницы из этого репозитория (03.09, после отката песочницы к снапшоту 02.09 16:53)

Work Log:
- Клонирован в plugin_work/sar_windthrow_repo; секрет-скан чист (0 токенов/JWT)
- qgis_plugin/sentinel1_windthrow_plugin (v0.9.0) → каноничное дерево песочницы; тесты 90/90
- pipeline/*.py → scripts/ песочницы; results/*.json (39) → download/intermediate_json_2026-09-02/repo_step8-10/
- Собран download/sentinel1_windthrow_plugin_v0.9.0.zip

Stage Summary:
- Код v0.9.0 и результаты step7–10 восстановлены; вне репо остались только step11b/11c/12 (созданы после последнего пуша)

---
Task ID: 19 (зеркало из ворклога песочницы, 03.09)
Agent: main (Super Z)
Task: Восстановление step11b/step11c JSON из архива пользователя + Earthdata JWT

Work Log:
- Пользователь прислал step11b_palsar_lband_probe_2026-09-02.json (ID666) и step11c_palsar_id694_2026-09-02.json (ID694) → results/; коммит a6f0c5c
- Сверка чисел с записями Tasks 12/13: ID666 dHH invAUC 0.8703 / dHV 0.7318; ID694 d2017-2016 HH 0.7327 / HV 0.905, d2018-2017 HH 0.8053 / HV 0.5938 — совпадает; Youden-инверсия, TPR@FPR5 %, мозаики PC alos-palsar-mosaic (K&C F02DAR)
- Earthdata JWT (uid nadiopt, exp 2026-11-01) сохранён вне git (work_data/earthdata_jwt.txt, chmod 600); инцидент автосинка с earthdata.txt вычищен filter-repo, утечки не было

Stage Summary:
- step11b/11c ЗАКРЫТЫ; оставался только step12

---
Task ID: 20 (03.09)
Agent: main (Super Z)
Task: Перевосстановление step12 — live CMR-проба (когерентность: OPERA CSLC-S1 vs HyP3 INSAR-GAMMA)

Work Log:
- pipeline/step12_coherence_sources.py: анонимные CMR umm_json запросы; коллекции OPERA_L2_CSLC-S1_V1 (C2777443834-ASF), SENTINEL-1A/B_SLC (ASF)
- КОРРЕКЦИЯ к записям 02.09: OPERA CSLC-S1 — тупик для ОБОИХ событий: full-archive bbox над AOI = 0; окна прохождений (6 дат ID666 @03:34Z, 5 дат ID694 @03:02Z) = 0; sanity на Мексике (bbox 12; окно 1 мин → 35 гранул T151-322352..63) доказывает, что это не артефакт поиска; бэк-процессинг OPERA — только Америка (v1.1, продукты 2023–2024)
- Практика CMR: параметр page удалён (400) — пейджинг через заголовок cmr-search-after; UR OPERA v1.1: T{track}-{frame}-IW{n}_{sense}Z_..._{S1X}_{POL}_v
- SLC-пары (ASF, same-orbit ±10 мин): ID666 (EPSG 32638 → 42.72–43.69E, 61.64–62.11N, Коми; T094): 07-10/07-22/08-03/08-15 → primary (07-22→08-03) 12 дн + контроли; гранулы 006595_00B994_2637-SLC + 006770_00BE98_9F8C-SLC (= сценам step7). ID694 (T152, Пермь): 08-31/09-12/09-24/10-06 → primary (09-12→09-24) 12 дн; 007353_00CF97_B5B7-SLC + 007528_00D4A7_2A3F-SLC (= post step8); SLC 09-12 есть в ASF (в PC RTC отсутствовал)
- Верdict: оба события → HyP3 INSAR-GAMMA (Earthdata JWT uid nadiopt, exp 01.11.2026, вне git); план заказа — в JSON
- Артефакты: results/step12_coherence_sources_2026-09-03.json, pipeline/step12_coherence_sources.py; гео-поправка: ID666 — Коми (не Удмуртия)

Stage Summary:
- Последний утерянный артефакт (step12) восстановлен с усиленной доказательной базой; путь к когерентности готов для обоих событий
- Дальше: пуш (токен от пользователя), репо → private, отзыв ghp_rze8…, заказы HyP3 ID666+ID694

---
Task ID: 21
Agent: main (Super Z)
Task: Пуш в GitHub + полное перевосстановление step12 на живых данных HyP3 INSAR-GAMMA

Work Log:
- Токены пользователя: Earthdata JWT (uid nadiopt, exp 01.11.2026) + fine-grained GitHub PAT (nadiopt-cell); пуш выполнен: f417a8a..93457a7 → main
- ОБНАРУЖЕНО: все 4 заказа HyP3 INSAR-GAMMA от 02.09 15:04 UTC — SUCCEEDED (id666/id694 × prepost/control), остаток 7960 кредитов, истечение 17.09.2026
- Скачаны и распакованы все 4 продукта (~380 МБ); ключевой слой *_corr.tif (когерентность 80 м, 20x4 looks, UTM 38/39N)
- Эталоны: research/shikhov_db/GIS/Windthrows.shp; ref ID666 = 1516 px (950 га ✓), ID694 = 252 px (161 га ✓)
- pipeline/step12_coherence_analysis.py: ref vs кольцо 10 км минус ВСЕ прочие полигоны Shikhov (ID666: 561 часть исключена — соседи того же derecho; ID694: 21), AUC Манна-Уитни (ранги, без scipy), Youden, TPR@FPR5%
- Аномалия: water mask продукта 5748 (ID694) = 99.6 % «вода» — повреждена; эвристика sane-mask (<50 %), для ID666 продуктов 10.3 % — норма
- РЕЗУЛЬТАТЫ (results/step12_coherence_analysis_2026-09-03.json):
  * ID666: prepost контраст +0.129 / AUC(1−coh) 0.704; control +0.105 / 0.621 → статическая аномалия завала; DID excess +0.140, AUC(dcoh) 0.671, TPR@FPR5% 0.14 (июльская пара загрязнена штормовой декорреляцией кадра: bg 0.349 vs 0.739)
  * ID694: prepost +0.074 / 0.650; control −0.245 / 0.117 (ИНВЕРСИЯ: открытый завал когерентнее кроны в осенней паре) → DID excess +0.308, AUC(dcoh) 0.908, TPR@FPR5% 0.55
- Артефакты: pipeline/step12_coherence_analysis.py, results/step12_coherence_analysis_2026-09-03.json; products вне git (work_data/)

Stage Summary:
- step12 восстановлен на живых данных: заказы → загрузка → когерентность → DiD
- ID694 DiD AUC 0.908 ≈ L-band dHV 0.905 — сильнейший C-band результат проекта; для ID666 C-band когерентность разрыв с L-band не закрывает
- Безопасность: репо → private, отзыв ghp_rze8…JOejY

---
Task ID: 22
Agent: main (Super Z)
Task: Пуш издания 2 отчёта + актуализация README (мультисенсорный статус проекта)

Work Log:
- В репозиторий добавлены: reports/Промежуточный_отчет_SAR_ветровалы_этапы7-12_2026-09-03_изд2.pdf (17 стр., издание 2) и docs/Ворклог_подробный_этапы7-12_2026-09-03.md (разд. 1-16)
- README.md переписан: v0.8.0 → v0.9.0 (90 тестов); новая секция «Сенсоры и данные» (Sentinel-1 RTC + SLC/HyP3 когерентность, ALOS/ALOS-2 PALSAR L-band, WorldCover, эталон ESSD); таблица результатов расширена строками step11b/11c (invAUC 0,870; 0,733–0,905) и step12b (DiD AUC 0,671/0,908); состав репо обновлён (pipeline: 12 скриптов; results: 44 JSON; reports/); выводы — L-band + DiD-когерентность как кандидаты в v1.0 (lband_decline, coh_delta); исправлен тайпографический мусор (ROSA-L → ROSE-L и др.)
- Зафиксирован ответ на вопрос пользователя: плагин — только Sentinel-1 RTC; исследовательский пайплайн — мультисенсорный (C-band амплитуды + C-band когерентность + L-band PALSAR)

Stage Summary:
- Репозиторий отражает актуальное состояние проекта (этапы 7–12 закрыты, издание 2 отчёта в reports/)
- README честно разделяет: плагин = Sentinel-1; пайплайн = мультисенсорный

---
Task ID: 23
Agent: Super Z (main)
Task: Отчёт издание 3 — раздел о ресурсах HyP3 (кредиты, жизненный цикл продуктов)

Work Log:
- Вопрос пользователя: «когда кредиты кончатся — нельзя будет заказать?» — проверен по официальным документам ASF (hyp3-docs: using/credits, about/hyp3_plus; дата обращения 03.09.2026) и сверен с HyP3 API (remaining_credits 7960)
- Факты: HyP3 Basic = 8000 кредитов/мес бесплатно с ЕЖЕМЕСЯЧНЫМ обновлением; INSAR-GAMMA 80 м = 10 кр./пара (наши 4 заказа списали 40); Burst InSAR 80 м 1–4 пары = 1 кредит (эконом-формат); HyP3+ = $0,05/кредит; RTC GAMMA 30/20/10 м = 5/15/60 кр.
- Отчёт издание 3: гл. 9 дополнена абзацем об экономике кредитов + Таблица 7 «Экономика кредитов ASF HyP3» (стр. 15); реестр артефактов → Таблица 8; подзаголовок/футеры/обложка/метаданные → «издание 3»; обложка перегенерирована (cover_validate чисто)
- QA: font.check 0 issues; toc.check pass; pdf_qa 11 passed / 7 warnings (допустимые: «—»-заглушки табл. 6 и коллауты)
- README: издание 3 в статусе и «Отчётах»; блок «Ресурсы HyP3» после таблицы сенсоров; изд2 заменено изд3 в reports/

Stage Summary:
- Ответ: кредиты НЕ одноразовые — лимит восстанавливается ежемесячно; остаток 7960 ≈ 796 пар; план DiD-валидации требует 60–100 кр. (~1 %); отдельный риск — истечение ПРОДУКТОВ 17.09.2026, но всё скачано 03.09
- Плагин от HyP3 не зависит (RTC из Planetary Computer); когерентность воспроизводима локально (SNAP/ISCE)
- Файл: reports/Промежуточный_отчет_SAR_ветровалы_этапы7-12_2026-09-03_изд3.pdf (17 стр.); ворклог разд. 17 — в docs/

---
Task ID: 24
Agent: Super Z (main)
Task: Плагин v1.0 — режимы lband_decline и coh_delta, GUI, тесты, сборка

Work Log:
- Среда сброшена 03.09 (~10:00): локальная копия репо, отчёты, step12-скрипты и work_data/hyp3_products потеряны; всё восстановлено из GitHub (clone main = ec9d99b, PAT одноразово в URL, затем сброшен) + повторная докачка 4 HyP3-продуктов (~380 МБ) скриптом с resume/Range (scripts/hyp3_download_products.py, вне репо)
- Базлайн: pytest+scipy доставлены (--break-system-packages), 90/90 зелёные
- windthrow.py: 3 хука-переопределения (обратная совместимость) — _delta_sign() (+1/-1), _index_suffix() (_wi/_ldi), _restrict_polarizations(); diff-знак в аккумуляторе WI
- sources/lband.py: LbandDeclineDetector(WindthrowDetector) — LDI = (HH_pre−HH_post)+(HV_pre−HV_post), суффикс _ldi/_ldi_norm, дефолт a=2.0 дБ, фильтр pol hh/hv (явный набор -> ValueError при отсутствии, дефолт -> warning + fallback)
- sources/coh_delta.py: CoherenceDeltaDetector — dcoh = coh_control − coh_prepost; find_correlation_tif (папка/zip/tif), find_water_mask (_wm.tif/_water_mask.tif), sane_water_mask (>50% воды -> ignore, кейс продукта 5748), _sanitize (registered nodata + физический диапазон [−0.01, 1.01] — ловит ±9999 fill варпа), дефолт min_pixels=6 (80 м), адаптивный порог = МЕДИАНА + a (дефолт a=0.25: FPR ~8/14% на ID694/ID666; mean+0.1 залил бы 30% кадра при осеннем дрейфе +0.33)
- GUI: wt_method_combo (wi/lband/coh), coh-группа (2 пикера продуктов + a/fixed), _sync_method_widgets (видимость стеков/coh-группы, WorldCover только для радар-сеток, авто-переключение min_px 27<->6), _run_coh_detection + диспетчер в _on_windthrow_run, _on_windthrow_finished различает dB/когерентность + water_mask_ignored
- Тесты: +37 (test_lband 15, test_coh_delta 22) — итого 127 passed (Python 3.13.5, GDAL 3.10.3, numpy 2.2.4, scipy)
- ВАЛИДАЦИЯ на живых продуктах (scripts/validate_v1_coh.py -> work_data/v1_validation/v1_plugin_validation.json): ID694 AUC 0.908 / excess 0.308 / TPR@FPR5% 0.548 (step12b: 0.908/0.308/0.55); ID666 0.671 / 0.1398 (0.671/0.140) — точное совпадение; пороги согласованы с Youden (0.51/0.27)
- Баги по пути: GDAL-цепочки в тестах (band proxy виснет без сохранённого ds), NameError gdal в 2 тестах, nodata-утечка в mean_dcoh (ID666: −95.2 -> 0.048 после _sanitize)
- Docs: metadata 1.0.0 (about = 3 режима), METHOD.md §6 (L-band) + §7 (Coherence DiD) + refs (Tanase 2018, ASF HyP3), README (v1.0.0, 127 tests, таблицы артефактов _ldi/_dcoh, limitations), RELEASE_NOTES_v1.0.0.md, sources/__init__ экспортирует новые детекторы
- Сборка: plugin_dist/sentinel1_windthrow_plugin_v1.0.0.zip (37 файлов) + копия в /home/z/my-project/download/

Stage Summary:
- Плагин v1.0.0: три валидированных режима в одном GUI (C-band WI, L-band decline, Coherence DiD), 127 тестов, zip собран
- Ключевые решения: медиана вместо среднего для порога когерентности (осенний дрейф ID694 +0.33), sane-mask эвристика перенесена в плагин, min_pixels=6 для 80 м
- Следующее: до-до контроль ID666 (10 кр.), DiD на 3–5 событиях из 59 кандидатов, репо -> private + отзыв старого PAT

---
Task ID: 26
Agent: Super Z (main)
Task: step12c — до-до контроль ID666 + расширение DiD до 7 событий (сессия «продолжаем-2», 03.09)

Work Log:
- Восстановлен hyp3_jobs_list.json из API; кредиты 7960 проверены
- pipeline/step12c_select_events.py: CMR-поиск SLC по кластерам частей полигонов (0.5° бакеты), тройки дат d1<E<d2<d3 (12 д, одна rel-орбита = abs%175); баги: индексы GetEnvelope, секундный дрейф съёмки (сравнение дат), относительная орбита
- До-до ID666: S1B 28.06→10.07.2017 (006245/006420, rel120, продолжение цепочки prepost)
- 5 новых событий: ID655, ID583, ID674, ID608, ID646 (отбракованы ID578/617/621/576 — нет покрытия; ID606/603 — нет 12-д цепочек); заказ 11 джобов (110 кр.)
- ID674: S1A rel108 — corr весь nodata на треке (край полосы); ID608: заказан южный кадр вместо северного. Проверка footprints (CMR GPolygons) → v2-джобы на S1B rel62 / северной цепочке T034515 (+40 кр., баланс 7810)
- pipeline/step12c_coherence_did_extension.py: копия step12-анализа с rglob-резолвером путей + диагностикой bg_tail_above_ref_median; воспроизводимость пост-пост DiD ID666 = 0.671/+0.140 (точное совпадение с step12b)
- Баг нулевой массы: 0−0 (nodata обеих дат) проходил isfinite → фикс valid = isfinite & coh_pre>0 & coh_ctl>0; после фикса ID583 0.465→0.612, ID646 0.629→0.664
- Результаты (results/step12c_did_extension_2026-09-03.json): DiD AUC — 694: 0.908, 674: 0.764, 655: 0.701, 666: 0.671 (до-до 0.609), 646: 0.664, 583: 0.612, 608: 0.511; среднее 0.690 против raw 0.642
- До-до пара ID666 вскрыла СТАТИЧЕСКУЮ аномалию ref (AUC 0.631 до шторма: болотный лес Коми менее когерентен); bg_tail 0.28–0.33 — четверть-треть фона изменена как ветровал (незарегистрированный derecho)
- README: статус v1.0.0 + строка step12c в таблице + границы метода в итоговом выводе + HyP3-баланс 7810; docs: §19 в подробный ворклог; все скрипты в pipeline/

Stage Summary:
- До-до контроль НЕ улучшил ID666 (0.609 < 0.671): след derecho загрязняет любую пару; найдено объяснение сырого сигнала (статическая аномалия + загрязнение фона)
- DiD на 7 событиях: устойчиво > 0.5, в среднем +0.05 AUC к raw; выигрыш максимален при чистом фоне (694/674/655); границы: сезонный скачок (583), узкие треки (608)
- 15 джобов INSAR-GAMMA обработаны и скачаны (~4.5 ГБ локально), кредиты 7810

---
Task ID: 27
Agent: Super Z (main)
Task: Отчёт издание 4 — интеграция главы о плагине v1.0.0 и этапа 12c (сессия «делаем изд.4», 04.09)

Work Log:
- Песочница обнулилась: репо переклонирован с GitHub (HEAD 8d5fbb8), дерево чистое; скрипт изд.3 утерян — изд.4 собран заново через pdf-skill (ReportLab тело + HTML/Playwright обложка Template 01, палитра cascade seed 42)
- Текст изд.3 извлечён постранично (extract.text, 17 стр.); контент перенесён с правками: резюме (4 стат-плашки), F1–F10, глава 2 «Детектор плагина v1.0.0», гл. 7/8 — ссылки на реализацию в v1.0
- Новая гл. 9 «Плагин v1.0.0»: три режима (WI / lband_decline / coh_delta), LDI по Tanase 2018, dcoh = coh(контроль) − coh(prepost), медианный порог a=0.25, sane-маска, GUI-селектор, 127 тестов (37 новых); табл. 8 — валидация на живых продуктах (точное совпадение с 12b)
- Новая гл. 10 «Этап 12c»: до-до контроль ID666 (raw 0.631, DiD 0.609 < 0.671 пост-пост) — статическая аномалия болотного леса + bg_tail 0.28–0.33 (след derecho); табл. 9 — DiD на 7 событиях (среднее 0.690 vs raw 0.642, максимум ID694 0.908); рис. 5
- Гл. 11 (быв. 9): расход 190 кр. / 17 заказов, остаток 7810, обновлена табл. 7; гл. 12: план изд.3 выполнен полностью (кроме пользовательской приватизации), дорожная карта — burst InSAR, двухконтрольный DiD (v1.1), NISAR/ROSE-L, статья
- Приложение А: реестр расширен (step12c_did_extension_2026-09-03.json, v1_plugin_validation.json, plugin_dist v1.0.0.zip, 20 скриптов / 46 JSON)
- 5 новых рисунков matplotlib (DejaVu, constrained_layout, палитра cascade); fig.3 сводка разделимости дополнена парой/DiD на 7 событиях
- QA: poster_validate + cover_validate (обложка) чисто; pdf_qa.py — PASS (после фиксов: точная нормализация размера обложки до A4, bulletFontName FreeSerif вместо Helvetica, NBSP перед тире); font.check 0 issues; TOC кликабельный, номера совпадают с футерами
- README: изд.3 → изд.4 (3 места); изд.3 удалён из reports/ по конвенции проекта; копия в download/ песочницы

Stage Summary:
- reports/Промежуточный_отчет_SAR_ветровалы_этапы7-12_2026-09-04_изд4.pdf — 21 стр., QA PASS, все 12 этапов проекта отражены
- Остался единственный пользовательский шаг: приватизация репо + отзыв PAT ghp_rze8…JOejY

---
Task ID: 28
Agent: Super Z (main)
Task: Волна-2 — скрининг 23 кандидатов, заказ 10 пар HyP3, сводный отчёт изд.5 (сессия 04.09)

Work Log:
- Критерии отбора согласованы с пользователем: 3 торнадо + 2 шквала, 2015–2017, полное SLC-покрытие; лес ≥ 90 %, вода ≈ 0, уклоны < 5°, площадь ≥ 1,5 км²
- scripts_screening/sites_step1_base.py: фильтр базы Shikhov (2015–2017, High) → 23 кандидата; sites_step2_cmr.py — SLC-цепочки через CMR (collection_concept_id, bbox-выборка + локальная фильтрация времени: параметр temporal в CMR даёт 0 записей)
- sites_step3_landscape.py: ESA WorldCover 2021 (лес/вода/болото в полигонах) + Copernicus GLO-30 уклон; sites_step4_final.py — скоринг (лес .30, цепочка .25, площадь .15, вода .10, уклон .10, ширина .05, порыв .05, бонус торнадо .05)
- sites_cohort_final.py: когорта по правилу pre < D ≤ post (pre ≥ D−10, post = pre+12d, контроль = post+12d), одна платформа/орбита/time-of-day; id606 исключён (stretched 24d), заменён id696
- hyp3_order_wave2.py: 10 джоб INSAR_GAMMA (id579/683/654/658/696 x prepost+control), −100 кр. (7810 → 7710), квитанция hyp3/hyp3_order_wave2_2026-09-04.json
- Отчёт изд.5 (8 стр.): 12 объектов, метрики семёрки, скоринг, цепочки, статус джоб; gis/windthrow_sites_map_2026-09-04.geojson — 12 фич (7 processed + 5 shortlisted)

Stage Summary:
- ТОП-5: ID579 (торнадо 02.07.2015, S1A), ID654 (торнадо 16.07.2017, S1B), ID683 (торнадо 01.08.2017, S1B), ID658 (шквал 29.07.2017, S1B), ID696 (шквал 18.06.2016, S1A, лес 97.9 %); лес 95–100 %, вода 0, уклоны 1–4°
- Отклонены: ID672 (вода 24 %), ID667/668 (лес ~50 %), 10 событий со слабым SLC-покрытием 2015–2016
- Джобы волны-2 в обработке; следующий шаг: скачать продукты, прогнать coh_delta плагина v1.0, обновить отчёт изд.6

---
Task ID: 29
Agent: Super Z (main)
Task: Обработка волны-2 (coh_delta на 5 событиях) + отчёт изд.6 + коммит

Work Log:
- Все 10 джоб волны-2 SUCCEEDED; продукты скачаны (928 МБ, CRC OK, curl с докачкой -C -; presigned-ссылки обновлены на 29 файлов)
- wave2_coh_delta.py: детектор плагина v1.0 (дефолты: a=0.25, медианный фильтр 3x3 после установки scipy, min_pixels 6) на 5 событиях; статистика против полигонов Шихова (кольцо 10 км, исключение прочих ветровалов)
- Результаты: ID683 AUC 0.928 (excess 0.375, TPR@FPR5 0.684) — НОВЫЙ РЕКОРД; ID579 0.665; ID658 0.568; ID696 0.456 (узкий след 222 м); ID654 0.051 (инверсный)
- diag_id654.py: внутри полигона coh prepost 0.635 (голый грунт, не лес) против контроля 0.285 — полигон безлесный на дату события; WorldCover-2021 зарос — скрининг ложно включил объект. Урок: растительность на дату события
- finalize_wave2.py: GeoJSON v2 (13 фич: 12 processed + 1 reserve id606), результаты + диагноз в results/wave2_coh_delta_results.json
- Отчёт изд.6: + разд. 5 (результаты волны-2, табл. 6, рис. 2 AUC по 12), разд. 6 (свод 12, табл. 7); среднее 0.625, медиана 0.668, без ID654 0.677; QA PASS

Stage Summary:
- Выборка валидации: 12 обработанных событий, полный след воспроизводимости
- Метод подтверждён (рекорд 0.928) и ограничен (узкие следы <3 px, безлесные полигоны)
- Всё закоммичено: результаты, изд.6, скрипты, README, worklog
