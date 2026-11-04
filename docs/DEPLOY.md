# RefundPilot · 部署与提交清单

按顺序做。每一步都写了“完成标准”，没达到就别往下走。

## 0. 准备凭证（只有你能做）

| 凭证 | 从哪拿 | 填到哪 |
|---|---|---|
| PayPal 沙盒 Client ID / Secret | developer.paypal.com → Apps & Credentials → Sandbox → Create App（类型 Merchant） | `backend/.env`，以及 Render 的 `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` |
| 沙盒 Personal 买家账号 | developer.paypal.com → Testing Tools → Sandbox Accounts（默认就有一个 personal 账号，点开看邮箱和密码） | 只在付款时用，不进代码 |
| Gemini API Key（主力） | aistudio.google.com → Get API key | `GEMINI_API_KEY` |
| OpenAI API Key（可选，备用） | platform.openai.com → API keys | `OPENAI_API_KEY`，再把 `OPENAI_MODEL` 设成账号可用的视觉模型；不配也能跑 |
| `MERCHANT_TOKEN` / `DEV_TOKEN` | 本地用 `change-me` 即可；Render 上由 `render.yaml` 自动生成随机值 | `MERCHANT_TOKEN` 会自动复制给前端、公开给评委（只能动沙盒的钱），`DEV_TOKEN` 不公开。部署环境里 token 为空或 `change-me` 时 API 会拒绝启动 |

**完成标准：** 本地 `GET /health` 返回 `"paypal": "PayPalClient"`，`"assessor": "chain"`。

## 1. 本地用真实沙盒走通

```bash
cd backend
rm -f data/refundpilot.db                       # 清掉离线模式造的 DEMO 订单
python scripts/mint_orders.py --count 8          # 每笔打印一个付款链接，用沙盒 Personal 账号付款（加 --batch 一次打印全部链接）
uvicorn app.main:app --reload
```

`mint_orders.py` 会把捕获好的订单追加到 `backend/seed/orders.json`，API 启动时自动导入。

**完成标准：** 浏览器里提交一笔申请并批准，详情页出现真实的 PayPal refund id，状态 `COMPLETED`；
在沙盒商家账号的交易记录里能看到这笔退款。

## 2. 换真实商品照片

**已完成（11-02）**：8 个商品都换成了白底商品图，放在仓库的 `assets/products/`，`backend/seed/products.json` 用 GitHub raw 地址引用，本地和线上都能访问。

**完成标准：** 用 5 张测试图（明显破损、轻微划伤、另一件商品、网上下载的图、模糊图）各提交一次，分类结果落在预期范围内。

## 3. 部署（免费：Render Web 服务 + Neon 数据库）

为什么这样搭：Render 的免费 Postgres 30 天后会被删，撑不到 12 月中的评审；Neon 免费版不过期。两个 Web 服务用 Render 免费版（`render.yaml` 里 `plan: free`，机房法兰克福）。

### 3.1 先推送订单
把 `backend/seed/orders.json`（只有沙盒订单号，没有密钥）提交推送，第一次部署就会导入这些订单。**确认 `.env` 没被提交**（`git status` 里不应出现）。

### 3.2 Neon 数据库
1. 打开 neon.tech → Sign up，用 GitHub 登录。
2. 新建项目：名字 `refundpilot`，Region 选 **AWS Europe Central 1 (Frankfurt)**，其他默认。
3. 项目首页点 **Connect**，把 **Connection pooling** 关掉，复制连接串（`postgresql://...neon.tech/neondb?sslmode=require...`）。先存进记事本，**不要发到聊天里**。

### 3.3 Render
1. 打开 render.com，用 GitHub 登录。免费套餐不收费；如果它要求验证付款方式，先停下来问。
2. Dashboard → **New → Blueprint** → 连接 GitHub 仓库 `gujguygfuiyf/refundpilot`。Render 读取根目录的 `render.yaml`，列出 `refundpilot-api` 和 `refundpilot-web` 两个服务。
3. 填 4 个值（都从 `backend\.env` 复制，Neon 的从记事本复制）：`DATABASE_URL`、`PAYPAL_CLIENT_ID`、`PAYPAL_CLIENT_SECRET`、`GEMINI_API_KEY`。`MERCHANT_TOKEN`、`DEV_TOKEN` 自动生成，前端的 `NEXT_PUBLIC_DEMO_MERCHANT_TOKEN` 自动复制商家 token，不用手填。
4. 点 **Deploy Blueprint**，等 5 到 10 分钟，两个服务都变成 Live。
5. 看两个服务的实际网址。如果 API 不是 `https://refundpilot-api.onrender.com`（名字被占用时 Render 会加后缀）：去 `refundpilot-web` → Environment，把 `NEXT_PUBLIC_API_URL` 改成 API 的实际网址，选 **Save, rebuild, and deploy**（这个值是构建时写进前端的）。web 的网址带后缀不用管，CORS 已经按正则放行。
6. 可选：要 OpenAI 备用，在 API 的 Environment 里加 `OPENAI_API_KEY` 和 `OPENAI_MODEL`。

免费版的代价：15 分钟没人访问就休眠，下一次访问约 1 分钟才醒（README 里已经提醒评委）；每月 750 小时免费额度，只在运行时计时，够用。Neon 闲置 5 分钟自动暂停，下次连接自动唤醒，不用管。

**评审期保持清醒**：`.github/workflows/keep-demo-awake.yml` 在 12 月 1 日 20:00 到 12 月 15 日 17:00（UTC）之间每 5 分钟访问一次两个服务，约用 666 小时，在 750 小时额度以内。它只访问 `/health`（不碰数据库），后端的后台任务也只在有退款等 PayPal 确认时才连数据库，所以 Neon 照样能休眠，不会用完每月 100 CU 小时的免费额度。录视频前想先叫醒网站：GitHub → Actions → keep-demo-awake → Run workflow。

**完成标准：** `https://<api>/health` 返回 `"paypal": "PayPalClient"`、`"assessor": "chain"`；web 首页能看到订单；在线提交一笔并批准成功。

## 4. 注册 Webhook

1. developer.paypal.com → 你的沙盒应用 → Webhooks → Add Webhook。
2. URL 填 `https://<api>.onrender.com/webhooks/paypal`，事件勾选 **Payment capture refunded**（`PAYMENT.CAPTURE.REFUNDED`）。
3. 在 Render 的 `refundpilot-api` → Environment → Add Environment Variable，加 `PAYPAL_WEBHOOK_ID` = 生成的 Webhook ID，保存后会自动重新部署。
4. 第一次收到真实事件时，在 Render 日志里看原始 JSON，核对 `resource.id` 是退款 id（后端按这个字段找退款记录）。

**完成标准：** 批准一笔后，Render 日志里有一次 webhook 请求返回 200，没有验签失败。

## 5. 给评委准备数据

- 再铸到 **40 笔以上**订单：每张订单只能申请一次，评审期到 12 月中。用 `python scripts/mint_orders.py --count 20 --batch`，一次打印全部付款链接，在浏览器标签里批量付。
- 不用担心退货窗口过期：没人申请过的订单会一直显示为“3 天前送达”（`DEMO_ROLLING_DELIVERY`）。
- 铸完提交推送 `seed/orders.json`，Render 会自动重新部署并导入；或者调用 `POST /dev/seed`（带 `Authorization: Bearer $DEV_TOKEN`，值在 API 的 Environment 页），线上直接导入。
- 提前提交一两笔示例申请（一笔待批准、一笔要求补证），这样评委一打开商家页就有内容可看。

## 6. 提交前清单（Devpost）

- [ ] 仓库公开，About 里能看到 MIT license
- [ ] 根目录 README 里的线上地址、视频链接已更新
- [ ] `git log -p | grep -iE "secret|api_key|sk-"` 没有真实密钥
- [ ] 线上 demo 可用，商家 token 已预填，订单卡上的剩余可退金额正常
- [ ] YouTube 视频公开，时长小于 3:00
- [ ] Devpost 表单：名称、tagline、描述、用到的技术、视频、仓库、demo 链接、测试说明（商家 token、沙盒说明）
- [ ] 赞助商奖勾选：Render、AG Grid（APIMatic 只在真的做了文档后再勾）
- [ ] 11 月 11 日前提交，留出 12 日的余量（德国时间 21:00 截止）
