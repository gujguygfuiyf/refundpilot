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

## 3. 部署到 Render

1. 先把 `seed/orders.json` 和新的 `products.json` 提交推送。**确认 `.env` 没被提交**（`git status` 里不应出现）。
2. Render Dashboard → **New → Blueprint** → 选 `gujguygfuiyf/refundpilot` 仓库，它会读根目录的 `render.yaml`，一次建好 `refundpilot-api`、`refundpilot-web`、`refundpilot-db`。
3. 创建时会要求填 `sync: false` 的变量：PayPal 两个、`PAYPAL_WEBHOOK_ID`（先留空，第 4 步再填）、`GEMINI_API_KEY`。`MERCHANT_TOKEN`、`DEV_TOKEN` 自动生成，web 服务的 `NEXT_PUBLIC_DEMO_MERCHANT_TOKEN` 自动复制 `MERCHANT_TOKEN`，不用手填；要看商家 token 的值，去 API 服务的 Environment 页。
4. 部署完成后检查两个服务的实际网址。如果名字被占用，Render 会加后缀，这时要同步修改：
   - API 的 `CORS_ORIGINS` 改成 web 的真实网址；
   - web 的 `NEXT_PUBLIC_API_URL` 改成 API 的真实网址，**改完要手动重新部署 web**，因为 `NEXT_PUBLIC_*` 是在构建时写进前端的。
5. 可选：要 OpenAI 备用，就在 API 服务的 Environment 里加 `OPENAI_API_KEY` 和 `OPENAI_MODEL`。

费用参考：两个 `starter` Web 服务加一个 `basic-256mb` Postgres，大约每月 20 美元。评审结束（12 月 15 日）之后再删。

**完成标准：** `https://<api>/health` 返回真实网关；web 首页能看到订单；在线提交一笔并批准成功。

## 4. 注册 Webhook

1. developer.paypal.com → 你的沙盒应用 → Webhooks → Add Webhook。
2. URL 填 `https://<api>.onrender.com/webhooks/paypal`，事件勾选 **Payment capture refunded**（`PAYMENT.CAPTURE.REFUNDED`）。
3. 把生成的 Webhook ID 填进 Render 的 `PAYPAL_WEBHOOK_ID`，重启 API。
4. 第一次收到真实事件时，在 Render 日志里看原始 JSON，核对 `resource.id` 是退款 id（后端按这个字段找退款记录）。

**完成标准：** 批准一笔后，Render 日志里有一次 webhook 请求返回 200，没有验签失败。

## 5. 给评委准备数据

- 再铸到 **40 笔以上**订单：每张订单只能申请一次，评审期到 12 月中。用 `python scripts/mint_orders.py --count 20 --batch`，一次打印全部付款链接，在浏览器标签里批量付。
- 不用担心退货窗口过期：没人申请过的订单会一直显示为“3 天前送达”（`DEMO_ROLLING_DELIVERY`）。
- 铸完提交 `seed/orders.json` 并重新部署 API；或者调用 `POST /dev/seed`（带 `Authorization: Bearer $DEV_TOKEN`），线上直接导入，不用重新部署。
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
