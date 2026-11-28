# 航次编排协作台

航运调度员、船长和港口代理共同编排航线、分配船舶、登记货物并跟踪每个航次的装载与到港状态。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 船舶档案 | `vessel` | 船舶 | 船名、总吨位、载重吨 |
| 航线编排 | `route` | 航线 | 起运港、目的港、经停港口 |
| 航次编排 | `voyage` | 航次 | 船舶编号、航线编号、计划离港 |
| 货物登记 | `cargo` | 货物 | 货物品名、托运方、件数 |
| 船员排班 | `crew_shift` | 排班单 | 船员姓名、岗位、上船日期 |
| 燃料记录 | `fuel_log` | 燃料记录 | 船舶编号、加油港口、燃料类型 |
| 港口挂靠 | `port_call` | 挂靠记录 | 航次编号、挂靠港口、预计到港 |
| 箱号登记 | `container` | 集装箱 | 箱号、箱型尺寸、封条编号 |
| 气象预警 | `weather_alert` | 预警通知 | 预警海域、预警类型、预警等级 |
| 事故记录 | `accident_log` | 事故记录 | 发生时间、发生海域、涉事船舶 |
| 船舶检验 | `inspection` | 检验记录 | 船舶编号、检验类型、检验机构 |
| 通关申报 | `clearance` | 申报单 | 航次编号、申报类型、申报港口 |
| 租船合约 | `charter` | 合约单 | 租用船舶、承租方、日租金 |
| 泊位安排 | `berth` | 泊位安排 | 港口名称、泊位编号、靠泊船舶 |
| 舱单编制 | `manifest` | 舱单 | 航次编号、货物清单、托运方汇总 |
| 航海日志 | `voyage_log` | 日志条目 | 航次编号、记录时刻、船位经纬度 |
| 船舶保养 | `maintenance` | 保养记录 | 船舶编号、保养项目、保养周期 |
| 船员证书 | `crew_cert` | 证书记录 | 船员姓名、证书类型、证书编号 |
| 调度指令 | `dispatch_note` | 指令单 | 接收船舶、指令内容、发出时间 |
| 费率核算 | `tariff` | 费率单 | 服务项目、计费单位、标准费率 |

## 复检办理

船舶检验与船舶保养共用一条“复检办理”链路，页面入口在 `船舶检验` 和 `船舶保养`
工作台顶部的复检办理清单（`frontend/src/components/RecheckPanel.vue`）。

- `GET /api/recheck?days=7`：筛选未来 7 日内到期的复检项目，按复检期限升序排序；
  `first` 是首条，`first_cooling` 是首条冷却复检，`moved_out` 是已移出窗口的项目，
  `accepted` 是已受理、仍可查原检验与原周期的安排。
- `POST /api/recheck/arrange`：发出安排请求并返回回执。同一检验按唯一键去重，
  并发或重复提交只保留一条办理，不会新增冷却复检；超出窗口的未受理项转为
  `等待周期更正`。
- `POST /api/recheck/{id}/accept`：受理安排，生成受理编号，并从原冷却检验提取证据。
- `POST /api/recheck/maintenance/{id}/cycle`：更正保养周期/补报下次保养日期，
  关联复检重新定位到窗口内外。
- `GET /api/recheck/maintenance/{id}`：从原保养工单恢复复检办理号、原检验和原周期。
- 保养工单的 `核验保养` 动作必须关联已受理的复检办理；三种状态分别是
  `已发出安排请求`（只发了请求，不能核验）、`已受理`（凭受理编号取证据核验）、
  `等待周期更正`（超出新期限，未受理项不能沿旧清单继续核验）。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：应用开发
子方向：web前端/全栈开发
