# US Job Hunter — 美国就业市场每日自动搜索

每天自动从 Adzuna、USAJobs、以及一批目标公司的 Greenhouse/Lever/Ashby 招聘接口中搜索职位，
用 Claude 对每个职位做匹配打分，过滤掉明确不提供签证担保 / 要求美国公民或安全审查的职位，
去重后把当天的新职位以 HTML 邮件摘要发送给你。

## 目录结构

```
main.py              # 主流程编排：抓取 -> 粗筛 -> 去重 -> 签证过滤 -> AI 打分 -> 发邮件
config.yaml           # 候选人档案、关键词、打分阈值、签证过滤短语、邮件设置
companies.yaml         # 目标公司清单及其 ATS 类型/token
db.py                 # SQLite 去重存储 (jobs.db)
filters.py            # 关键词粗筛 + 签证规则过滤（纯函数，易单测）
scoring.py            # 调用 Anthropic API 做 AI 匹配打分
sponsorship_data.py    # DOL 历史签证担保数据（H-1B/LCA）查询，给 AI 判断提供真实数据佐证
notify.py             # 生成 HTML 邮件并通过 Resend/SendGrid/Gmail 发送
sources/
  adzuna.py            # Adzuna API 适配器
  usajobs.py           # USAJobs API 适配器
  ats_boards.py        # Greenhouse/Lever/Ashby 公开职位接口适配器
  common.py            # 共享的 HTTP 请求 / 地点判断 / HTML 清洗工具函数
.github/workflows/daily.yml  # 每日定时运行的 GitHub Actions 配置
jobs.db               # SQLite 数据库（已提交到仓库，用于跨运行去重，见下文）
```

## 候选人档案

打分所用的候选人背景写在 `config.yaml` 的 `candidate_profile` 字段里，目前是占位内容
（德国籍 / 瑞士居住 / TUM 机械+航空硕士+增材制造博士 / Ivoclar 牙科3D打印研发经验，
需要签证担保）。**拿到完整英文简历后，直接编辑这一段文字即可**，不需要改代码，
下次运行就会用新的背景做打分。

## 快速开始（本地测试）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，至少填入 ANTHROPIC_API_KEY 才能跑通打分环节；
# Adzuna / USAJobs 的 key 留空时程序会自动跳过对应数据源（不会报错中断）

set -a && source .env && set +a
python main.py --dry-run
```

`--dry-run` 模式只会把当天匹配到的职位打印到终端，**不会发送邮件、也不会写入数据库**，
可以反复运行用于调试关键词/阈值/公司清单。

正式运行（会发邮件、会写数据库）：

```bash
python main.py
```

## 需要申请的 API Key

| 用途 | 服务 | 申请地址 | 必填 |
|---|---|---|---|
| AI 打分 | Anthropic API | https://console.anthropic.com/ | 必填 |
| 职位搜索 | Adzuna API | https://developer.adzuna.com/ （免费注册即得 app_id / app_key） | 可选（建议填） |
| 职位搜索 | USAJobs API | https://developer.usajobs.gov/apidocs （需要一个邮箱作为 User-Agent） | 可选 |
| 发邮件 | Resend（推荐） | https://resend.com/ （免费额度：每天 100 封 / 每月 3000 封，HTTP API 无需 SMTP 密码） | 三选一 |
| 发邮件 | SendGrid | https://sendgrid.com/ | 三选一 |
| 发邮件 | Gmail SMTP | 无需注册第三方服务，只需一个 [Google 账号应用专用密码](https://myaccount.google.com/apppasswords)（要求开启两步验证） | 三选一 |

**为什么推荐 Resend**：注册即用、免费额度对每日一封摘要邮件绰绰有余、只需一个 API key，
不用像 Gmail SMTP 那样折腾"应用专用密码"和两步验证。如果你已经有可用的 Gmail 应用专用密码，
用 `gmail_smtp` 也完全没问题——把 `config.yaml` 里 `email.provider` 改成 `gmail_smtp` 即可。

## 配置 GitHub Secrets

在仓库 `Settings -> Secrets and variables -> Actions` 中添加以下 Secrets
（用哪个邮件服务就只填对应那组，其余留空即可）：

必填：
- `ANTHROPIC_API_KEY`
- `EMAIL_TO`（你的收件邮箱）

Adzuna（推荐填，职位来源之一）：
- `ADZUNA_APP_ID`
- `ADZUNA_APP_KEY`

USAJobs（可选，联邦职位大概率要求美国公民，会被签证过滤器剔除大部分）：
- `USAJOBS_API_KEY`
- `USAJOBS_USER_AGENT_EMAIL`

邮件（三选一）：
- Resend: `RESEND_API_KEY`, `EMAIL_FROM`（在 Resend 后台验证过的发件地址，测试阶段可先用
  `onboarding@resend.dev`）
- SendGrid: `SENDGRID_API_KEY`, `EMAIL_FROM`
- Gmail SMTP: `GMAIL_SMTP_USER`, `GMAIL_SMTP_APP_PASSWORD`

同时把 `config.yaml` 里 `email.provider` 改成你实际使用的服务名
（`resend` / `sendgrid` / `gmail_smtp`）。

## 定时任务与夏令时

`.github/workflows/daily.yml` 中的 cron 是 `0 13 * * *`（UTC），对应美东**夏令时**
（EDT，UTC-4）的早上 9:00。每年 11 月初到次年 3 月初是**冬令时**（EST，UTC-5），
届时需要手动把 cron 改成 `0 14 * * *`，否则邮件会在美东时间早上 8 点发出。
GitHub Actions 的 cron 不支持时区/夏令时自动切换，如果你想完全自动化，可以在
`main.py` 里加一段用 `zoneinfo` 判断当前是否夏令时、不满足则直接 `sys.exit(0)`
提前退出的逻辑——当前版本为保持简单未实现这一层，按需自行改动。

你也可以在 GitHub 仓库的 Actions 页面手动点击 "Run workflow" 立即触发一次运行
（用于验收测试）。

## 数据去重与持久化 (jobs.db)

所有见过的职位（无论是否入选邮件）都记录在 SQLite 文件 `jobs.db` 里，
唯一键是 `数据源:职位ID`（没有 ID 时用 `公司+标题+地点` 的哈希代替）。
已经推送过 / 已经判断过的职位不会重复出现，也不会重复送去 AI 打分（省钱）。

**持久化方案**：`jobs.db` 直接提交（commit）在仓库里，工作流每次运行结束后会自动把
更新后的 `jobs.db` commit 并 push 回仓库（见 `daily.yml` 最后一步）。相比 GitHub Actions
cache（有 7 天未使用即被清理、10GB 仓库配额等限制，且不保证严格顺序），提交回仓库更简单可靠，
代价是仓库里会有一个体积会缓慢增长的二进制文件——对于个人使用场景（每天几十到上百条职位）
增长速度可以忽略。如果职位量很大导致仓库体积成为问题，可以之后改为定期归档旧记录。

## 如何增删目标公司 (companies.yaml)

直接编辑 `companies.yaml`，每一项：

```yaml
- name: "公司名称"
  ats: "greenhouse"   # greenhouse | lever | ashby | none
  token: "board-token"
```

`ats: none` 表示该公司用的是 Workday / Taleo / SmartRecruiters / Jobvite / UltiPro /
Oracle Cloud 等系统，没有公开匿名 JSON 接口，程序会跳过（这是本项目"只用 API 和公开
ATS 端点，不爬 LinkedIn/Indeed 页面"这一约束下的已知限制）。

### 验证/查找一个公司的 ATS token

- **Greenhouse**：打开该公司招聘页，URL 形如 `https://job-boards.greenhouse.io/<token>/jobs/...`
  （部分公司仍用旧域名 `boards.greenhouse.io/<token>`）；也可以直接在浏览器访问
  `https://boards-api.greenhouse.io/v1/boards/<token>/jobs` 看是否返回 JSON。
- **Lever**：招聘页 URL 形如 `https://jobs.lever.co/<token>`；接口地址
  `https://api.lever.co/v0/postings/<token>?mode=json`。
- **Ashby**：招聘页 URL 形如 `https://jobs.ashbyhq.com/<token>`；接口地址
  `https://api.ashbyhq.com/posting-api/job-board/<token>`。

`companies.yaml` 中当前的 token 是通过搜索引擎中出现的真实职位链接核实的，但本项目开发环境
本身无法直接发起网络请求做最终验证，**首次使用前建议按上面的方法手动访问一次对应接口 URL
确认能返回 JSON**，尤其是标了 `note` 但仍给了 token 的条目。

## 如何调整关键词 / 打分阈值 / 签证过滤短语

全部在 `config.yaml` 里，改完直接生效，不需要碰代码：

- `search.keywords`：职位标题/描述命中任意一个短语即可通过粗筛
- `prefilter.max_jobs_to_ai_per_run`：每次运行最多送多少个职位给 AI 打分（控制成本）
- `scoring.score_threshold`：入选邮件所需的最低匹配分（0-100）
- `scoring.model`：使用的 Claude 模型名。需求文档中原写的 `claude-sonnet-4-6` 并非真实存在
  的模型 ID，已改为当前实际可用的 `claude-sonnet-5`；如需切换到之后发布的更新模型，改这一行即可
- `visa_filter.reject_phrases`：命中即直接丢弃的签证/安全审查相关短语（小写子串匹配）
- `visa_filter.positive_phrases`：明确提及"提供签证担保"的正向短语，AI 判断时参考

## 每日邮件

- 有新职位时：按匹配分从高到低排列，每条含职位名（可点击链接）、公司、地点、匹配分、
  签证判断标签（绿=可能提供担保 / 灰=不确定；`unlikely` 的职位已经在打分阶段被剔除，
  不会出现在邮件里）、AI 给出的一句话中文理由。
- 邮件末尾附当日统计：各数据源抓取总数、关键词粗筛通过数、去重后新职位数、
  规则剔除数、送 AI 打分数、最终推送数。
- 没有新职位时会发一封简短的"今日无新增"邮件，用于确认程序仍在正常运行。

## 历史签证担保数据（H-1B/LCA disclosure data）

签证判断原本完全靠 AI 猜职位描述的字面意思，容易被职位描述里没提到签证的情况带偏。
这个功能给 AI 提供一个真实数据佐证：从美国劳工部（DOL）公开的 LCA（H-1B/H-1B1/E-3）
披露数据里查这家公司历史上提交过多少次相关申请、批了多少次，把这个数字写进 AI 打分的
prompt 里，也会显示在邮件对应职位下面（比如"公开数据：该公司近期披露 12 起 H-1B/E-3
申请，10 起获批（约 83%）"）。

**如何配置**：`config.yaml` 的 `sponsorship_data` 一节。
- 最推荐的方式：自己去 https://www.dol.gov/agencies/eta/foreign-labor/performance
  找到当前最新一期 "H-1B Disclosure Data" 文件（xlsx 格式）的下载直链，填进
  `sponsorship_data.resource_url`。这个文件每季度换一次，需要你偶尔手动更新一下这个链接
  （比如每季度看一眼）。
- 如果 `resource_url` 留空，程序会尝试通过 data.gov 的开放数据 API（CKAN，
  `catalog.data.gov/api/3/action/package_show`）自动找最新一期文件——图省事但不如手动填
  可靠，这个数据集的接口结构如果被 DOL/data.gov 调整过，自动发现可能会失效。
- 数据每季度才更新，所以不会每天都重新下载，由 `refresh_max_age_days`（默认 30 天）控制
  多久重新抓一次；抓取结果缓存在 `jobs.db` 里的两张新表（`employer_sponsorship` /
  `sponsorship_meta`），随 `jobs.db` 一起持久化。
- 不想用这个功能就把 `sponsorship_data.enabled` 设为 `false`，其余流程完全不受影响。
- 这张缓存表不受 `--dry-run` 影响（`--dry-run` 只是不把职位写入去重库、不发邮件，跟"当天处理
  进度"无关的这张参考数据表照常按 `refresh_max_age_days` 的节奏刷新/复用），方便本地反复
  测试时不用每次都重新下载。

**已知的局限，务必了解**：
1. **公司名匹配是最大的短板**：政府披露数据里存的是公司的"法律实体名"，跟职位页面上的
   品牌名往往对不上（比如 SpaceX 的法律实体名是 "Space Exploration Technologies Corp"）。
   程序会先做精确匹配（自动去掉 Inc/LLC/Corp 等后缀再比较），匹配不上的话可以在
   `companies.yaml` 对应公司条目里加一个 `legal_name` 字段手动指定，`SpaceX` 已经作为示例
   配好了。查不到匹配的公司，AI 打分时就是"没有这项数据"，不会瞎猜。
2. **这个自动化流程本身没有在真实环境跑通验证过**：开发这个功能时的沙盒环境网络策略连不上
   dol.gov / data.gov，所以下载和解析这部分只做了用假数据模拟的单元测试，逻辑测试通过，
   但**第一次在 GitHub Actions 里实际跑的时候请留意日志**，确认它真的抓到数据了
   （日志里会有 `job_hunter.sponsorship_data` 相关的 INFO/WARNING/ERROR）。如果自动发现失败，
   按上面说的手动填 `resource_url` 是更可靠的备选方案。
3. 数据是"当季快照"，不是历史累计——只反映最近一个季度提交的申请，不代表这家公司过去
   几年的全部担保记录。

## 已知限制

- Adzuna 免费 API 返回的是职位描述的**摘要/片段**而非完整正文，签证过滤和 AI 打分的
  准确度会受此影响；Greenhouse/Lever/Ashby 三种接口能拿到完整职位正文，判断更可靠。
- 目标公司中相当一部分（Align Technology、Dentsply Sirona、SpaceX 之外的多数航空航天公司等）
  使用 Workday 或其他没有公开匿名 JSON 接口的系统，程序会跳过这些公司——这是"只用 API 和
  公开 ATS 端点，不做网页爬虫"这一约束下的必然限制，`companies.yaml` 里已逐条注明。
- 签证过滤第一层是关键词规则匹配，可能有漏网之鱼或误伤，第二层交给 AI 判断
  `sponsorship_likelihood` 做二次把关，但最终建议仍以职位详情页描述为准。

## 验收清单

- [ ] `python main.py --dry-run` 能跑通并打印出真实职位与打分
- [ ] GitHub Secrets 已按上表配置完成
- [ ] 在 Actions 页面手动触发一次 workflow 运行成功
- [ ] 收到邮件，格式清晰（或收到"今日无新增"确认邮件）
- [ ] `jobs.db` 在 workflow 运行后被自动提交回仓库
- [ ] 需要时能自行编辑 `companies.yaml` / `config.yaml` 调整公司、关键词、阈值
- [ ] 查看一次运行日志，确认历史签证担保数据（`sponsorship_data`）成功抓取（或按 README
      说明手动填 `resource_url` 后再确认一次）
