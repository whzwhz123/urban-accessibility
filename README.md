## 本轮功能开发（2026-10-25，尚未运行验证）

开发基线为 GitHub `main` 的 `ec3c15c`。下述功能已纳入本轮开发快照；具体提交以 Git 历史为准。

- 增加无全国数据集时的城市注册、自有数据字段识别与映射、POI类别映射和质量摘要。
- 增加时刻表CSV导入：固定间隔或离散班次转换为早高峰7–9时、平峰9–17时、晚高峰17–19时的间隔；缺失值保留待补，早晚高峰可独立提供，兼容原peak/flat格式。
- 接通城市数据准备、共享需求、多时段分配和已有采集指标评价的批量流程；可停止和恢复未完成步骤，不自动采集高德数据。需求/评价恢复可能重做未完成步骤，不等于逐运算断点续算。
- 增加历史实验搜索与分页、同条件/分时情景对照、联动地图与路段差值展示、GeoPackage和Shapefile导出、按需生成可打印报告、地图PNG和打印PDF。绝对客流仍采用绿—黄—红，差值采用发散色标；缺失指标不按零客流处理。操作面板可收起，临时叠加图层明确声明坐标、不会参与计算。
- 增加工程文件保存与手动打开。默认保存配置和引用，可附带本市处理后POI输入快照、真实线网、分时发车数据及结果，不复制全省POI。原始附件隔离保留；用户勾选恢复输入时，可注册缺失城市并创建、启用新的输入版本，再准备数据继续计算。同名结果内容冲突则整组跳过，旧输入与旧结果不覆盖；历史图可只读查看。工程包不包含可执行pickle断点，不承诺跨电脑继续迭代。
- 增加 `Install.cmd` / `Start.cmd`。安装器创建本地`.venv`，需要Python 3.12及Node.js。启动优先使用`.venv`，仍支持`URBAN_PYTHON`和`URBAN_DATA_ROOT`。没有全国数据集时可先启动、注册城市并上传。

**状态边界：以上为代码实现和接口接线，本轮按用户要求未运行测试、构建、求解、真实API采集或浏览器验收，也没有执行安装器。不应把本节当作功能验收结论。现有分配数学模型、默认数值与历史实验未改写。**

以下保留历史说明，旧版现状或限制与本节不同时以本节开发状态为准。

## 当前可运行版本（2026-10-13）

本节替代后文旧版本的现状描述；旧说明保留供追溯。

### 启动

```powershell
npm install --legacy-peer-deps
py -m pip install -r Back_end/requirements.txt
npm start
```

网页为 http://127.0.0.1:3000，本地API为8765端口。启动先检查依赖与全国城市目录。Python可通过URBAN_PYTHON指定，数据根可通过URBAN_DATA_ROOT指定。真实密钥不得提交Git；页面输入JS Key和securityJsCode只保留在内存，也可使用本地忽略的.env。

### 使用顺序

1. 数据管理选择城市，准备本地全国线网和省级POI；也可上传标准ZIP作为新的计算输入。支持CPTOND SHP或标准GeoJSON加POI CSV，必须明确WGS84/GCJ02/投影EPSG。原始文件及导入版本保留，不覆盖全国源数据。单纯GeoJSON展示上传不是计算数据导入。
2. 需求配置设置POI重要性、分时发生/吸引强度及小时总需求，生成共享需求版本。非北京城市不套用北京需求。数据源变化后旧版本标为失效，需要准备并重建。
3. 分配先检查规模，默认K=3、接入站最多3个、步行1km、OD 1–20km、最多2次换乘、惩罚2min、公交18km/h、车辆80人、最多500轮、gap=1e-4。K作用于每个换乘次数。上传发车间隔优先，缺失默认15min，可修改并记录来源。
4. 分配支持任务状态持久化、安全边界停止、可信本地断点恢复；相同数据、参数和代码签名且输出校验通过时复用完整结果。代码/输入变化会拒绝旧断点恢复。预检给理论上界，路径生成后保存实测矩阵规模，不能将上界当实际内存或可靠耗时预测。
5. 选城和需求版本后在分析设置采集等时圈与导航。默认每秒最多1次串行请求，每10条或30秒分批存服务端；关闭页面后可选任务恢复。软件请求预算不是账号真实剩余额度；权限或配额错误停止。高德现行额度以控制台为准，不能把旧版日配额表当作现行账户余额。
6. 选择采集版本和参数后计算覆盖度、机会、竞争力、熵权综合与供需分类。提交时固定文件；45min与30min数据不能混用。地图查看与ZIP导出包含原始采集、共享需求、实际参数、结果及清单，不导出pickle断点。

### 方法与限制

- 默认机会评分为累计可达POI重要性，不估造逐POI公交耗时。多时间档模式以15min步长及最终阈值获取首次可达区间，以高斯衰减计算机会评分上下界；缺档位不补零，非嵌套边界记录质量问题。历史程序的距离比例近似保留为明确命名模式，不称为精确时间或论文逐公式复现。论文PDF第44页也使用距离比例，但搜索半径定义与旧程序边界外包框尺度不同。
- 导航终点按1–5、5–10、10–20km分层、不重复采样；它是空间诊断样本，不是实际需求加权OD预测。记录样本数、标准差及前缀样本稳定性；少数终点不能证明总体预测精度。
- 竞争力为mean(car_time/(bus_time+alpha*transfers))。API时间已含步行和候车，alpha仅为额外感知惩罚。无线路、接口错误、超筛选上限分开记录，不把缺失值补零。地图归一化为样本内显示，不是跨城市绝对排名。
- 分时导航只在北京时间早高峰7–9时、平峰9–17时、晚高峰17–19时实际采集。可在页面等待时段，但必须保持页面打开；未采集的时段不伪造。ArrivalRange使用静态公交信息，不虚构三个时段的动态等时圈。
- 当前分配为共同线路候选路径MSA加容量再分配。desired流量的gap与served流量的容量可行性不同；不能将后者称为严格容量用户均衡或逐班次拒载仿真。
- 全量需求与覆盖度可以计算；有限等时圈/导航只评价实际有数据的格网，不插值冒充全城评分。

### 验证和Git

```powershell
py scripts/test_city_workflow.py
py scripts/test_amap_and_headways.py
py scripts/test_custom_inputs.py
py scripts/test_assignment_tasks.py
py scripts/test_collection_and_scoring.py
node scripts/test_arrival_collector.cjs
npm run build
```

代码、依赖锁和测试纳入版本管理；全国数据、自有输入、缓存、断点、任务记录、实验及密钥保留在忽略目录。不要加入真实凭证，不删除原始数据或旧实验。

# Urban Accessibility Rebuild

城市公交可达性评估系统（Urban Accessibility Analysis System）。

本项目面向“公交服务是否匹配城市需求”的综合评估，输出三类核心空间指标：

- `Demand`：需求密度（POI + 人口校准）
- `Isochrone`：公交等时圈可达性（30/45min）
- `Coverage`：覆盖度（步行便捷度 + 线路多样性）

---

## 1. 系统定位

系统分为两个互补部分：

- `离线计算引擎（Back_end/data_process）`
- `在线可视化交互界面（React + DeckGL）`

离线引擎负责多源数据融合与指标计算；前端负责地图交互、图层控制、指标解释和结果展示。

---

## 2. 总体技术架构

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                          Data Source Layer                               │
│  POI CSV / Bus JSON / LandScan TIF / AMap Isochrone API                 │
└───────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                        Offline Processing Layer                          │
│  preprocess.py  -> demand_grid + lines + stops                           │
│  isochrone fetch -> isochrone_30/45 raw                                  │
│  isochrone_score.py -> isochrone_grid                                    │
│  Coverage.py -> coverage_grid                                             │
└───────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          Data Serving Layer                              │
│  public/data/output/*.geojson + summary.json                              │
└───────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                         Frontend Application Layer                        │
│  MainPage -> SidebarPanel + MapContainer                                  │
│  DeckGL GeoJsonLayer rendering + tooltip + legend                         │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 工程架构（现状实现）

> 下面是当前仓库中已经落地的结构。

```text
urban-accessibility-rebuild/
├── Back_end/
│   ├── data_process/
│   │   ├── preprocess.py
│   │   ├── isochrone_fetch_puppeteer.js
│   │   ├── isochrone_AmapJS.py
│   │   ├── isochrone_score.py
│   │   ├── Coverage.py
│   │   ├── README.md
│   │   └── output/
│   └── landscan-global-2022-assets/
├── public/
│   ├── index.html
│   ├── favicon.ico
│   └── data/output/
├── src/
│   ├── components/
│   │   ├── Map/MapContainer.jsx
│   │   └── Sidebar/
│   │       ├── SidebarPanel.jsx
│   │       ├── DataUpload.jsx
│   │       └── AnalysisPanel.jsx
│   ├── pages/MainPage.jsx
│   ├── styles/
│   ├── App.jsx
│   └── index.jsx
├── package.json
├── config-overrides.js
└── README.md
```

---

## 4. 目标完整架构（建议形态）

> 这是你给出的更完整工程分层，我保留为“目标架构蓝图”，方便后续迭代对齐。

```text
urban-accessibility-rebuild/
├── public/
│   ├── index.html
│   └── favicon.ico
├── src/
│   ├── components/
│   │   ├── Map/
│   │   │   ├── MapContainer.jsx
│   │   │   ├── HexagonLayer.jsx
│   │   │   ├── BusRouteLayer.jsx
│   │   │   └── DrawTool.jsx
│   │   ├── Sidebar/
│   │   │   ├── DataUpload.jsx
│   │   │   ├── AnalysisPanel.jsx
│   │   │   └── ResultsPanel.jsx
│   │   ├── UI/
│   │   │   ├── HexagonGridControls.jsx
│   │   │   └── BusRouteControls.jsx
│   │   └── Algorithm/
│   │       ├── AccessibilityWorker.js
│   │       └── GraphUtils.js
│   ├── pages/
│   │   └── MainPage.jsx
│   ├── utils/
│   │   ├── hexagon.js
│   │   ├── busDataParser.js
│   │   ├── accessibility.js
│   │   └── fileHandler.js
│   ├── hooks/
│   │   ├── useMapData.js
│   │   └── useAccessibility.js
│   ├── App.jsx
│   ├── index.jsx
│   └── styles/
│       ├── App.css
│       └── index.css
├── package.json
├── config-overrides.js
└── README.md
```

---

## 5. 前端架构详解

### 5.1 页面编排层

- `MainPage.jsx` 负责全局状态编排。
- 左侧 `SidebarPanel` 负责输入与控制。
- 右侧 `MapContainer` 负责可视化输出。

### 5.2 交互控制层

- `DataUpload`：导入 GeoJSON、清空视图。
- `AnalysisPanel`：图层开关（demand/isochrone/coverage/lines/stops）。
- 目标扩展可加入 `ResultsPanel` 展示统计结果与导出能力。

### 5.3 地图渲染层

- 基于 `react-map-gl + deck.gl`。
- 每个指标一个 `GeoJsonLayer`。
- tooltip 展示网格属性、站点属性。
- 图例由激活图层动态驱动。

### 5.4 目标可演进层（建议）

- 将 `MapContainer` 拆分为：
  - `HexagonLayer`
  - `BusRouteLayer`
  - `StopLayer`
  - `DrawTool`
- 将复杂计算迁移至 `WebWorker`，避免主线程卡顿。
- 通过 `hooks` 管理数据与分析状态，减少页面耦合。

---

## 6. 后端数据处理架构详解

### 6.1 preprocess.py（基础网格构建）

输入：

- POI CSV（多分类）
- 公交线网 JSON
- 人口栅格 TIF

处理：

- POI 合并与权重映射
- 200m 六边形网格化
- 空间平滑与归一化
- 人口采样校准（形成 `demand_final`）
- 公交线路与站点结构化

输出：

- `demand_grid.geojson`
- `bus_lines.geojson`
- `bus_stops.geojson`
- `summary.json`

### 6.2 isochrone 处理链

采集层：

- `isochrone_fetch_puppeteer.js`（自动化采集）
- 或 `isochrone_AmapJS.py`（生成浏览器控制台脚本）

评分层：

- `isochrone_score.py`
- 计算 `iso_score_30m_norm`
- 计算 `iso_score_45m_norm`
- 融合 `iso_score_combined`

输出：

- `isochrone_grid.geojson`

### 6.3 Coverage.py（覆盖度）

计算维度：

- 最近站点距离 `nearest_stop_m`
- 步行便捷度 `walk_score`
- 500m 线路多样性 `diversity_score`
- 综合覆盖度 `coverage_score`

输出：

- `coverage_grid.geojson`

---

## 7. 关键数据契约（Data Contract）

### 7.1 demand_grid.geojson

核心字段：

- `poi_count`
- `demand_raw`
- `demand_kde_norm`
- `population`
- `pop_norm`
- `demand_final`

### 7.2 isochrone_grid.geojson

在 demand 字段基础上增加：

- `iso_score_30m`
- `iso_score_30m_norm`
- `iso_score_45m`
- `iso_score_45m_norm`
- `iso_score_combined`

### 7.3 coverage_grid.geojson

在 demand 字段基础上增加：

- `nearest_stop_m`
- `walk_score`
- `line_count_500m`
- `diversity_score`
- `coverage_score`

---

## 8. 指标计算流程（端到端）

```text
POI/公交/人口数据
  -> preprocess.py
  -> demand_grid + lines + stops
  -> isochrone fetch (30/45min)
  -> isochrone_score.py
  -> isochrone_grid
  -> Coverage.py
  -> coverage_grid
  -> public/data/output
  -> 前端多图层可视化
```

---

## 9. 本地运行

### 9.1 前端

```bash
npm install
npm start
```

### 9.2 Python 处理依赖

```bash
pip install transbigdata geopandas rasterio shapely scipy pandas numpy pyproj
```

### 9.3 等时圈采集依赖

```bash
npm install puppeteer
```

### 9.4 推荐执行顺序

```bash
python Back_end/data_process/preprocess.py
node Back_end/data_process/isochrone_fetch_puppeteer.js
python Back_end/data_process/isochrone_score.py
python Back_end/data_process/Coverage.py
```

---

## 10. 当前数据规模（依据现有 summary.json）

- `grid_count`: 1462
- `poi_total`: 75323
- `bus_lines`: 3967
- `bus_stops`: 17383

---

## 11. 架构演进建议

- 从“单组件集中渲染”演进到“图层组件化渲染”。
- 从“页面本地状态”演进到“hooks + worker 分层状态”。
- 从“脚本手动串行执行”演进到“一键 pipeline（Makefile/npm scripts）”。
- 从“本地文件发布”演进到“API/对象存储版本化数据发布”。

