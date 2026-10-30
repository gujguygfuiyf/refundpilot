# RefundPilot · 3 分钟演示脚本

目标时长 **2:45**（硬上限 3:00，Devpost 要求严格小于 3 分钟）。旁白用英文，下面每段给出画面、操作、旁白和要传达的点。
录制前按文末“开拍前检查”逐项过一遍。

## 素材准备

| 素材 | 说明 |
|---|---|
| 案例 A：破损杯子 | 同一个杯子：完好的照片做商品图，把手断裂（或贴胶带模拟裂缝）的照片做买家照片。预期命中 `R1-broken`，全额退款 |
| 案例 B：拍错商品 | 下单的是杯子，买家照片却是另一件东西（比如水杯）。预期命中 `R4-mismatch`，要求补证，不动钱 |
| 订单 | 线上至少 3 笔未退过款的真实沙盒订单（A、B 各一笔，再留一笔备用） |
| 窗口 | 浏览器两个标签：买家页 `/`、商家页 `/merchant`；第三个标签开 PayPal 沙盒商家后台的交易记录页 |
| 分辨率 | 1920×1080，浏览器缩放 110%，关掉书签栏和通知 |

## 分镜

### 0:00 – 0:18 · 问题

**画面：** 黑底白字标题卡，然后切到订单页。

> When an item arrives broken, the buyer wants their money back and the merchant has to decide: is the photo real, is it the right product, and how much is fair? Slow answers turn into PayPal disputes, and disputes cost merchants fees and time. RefundPilot resolves the problem before it becomes a dispute.

**要点：** 问题真实，用户是谁。

### 0:18 – 0:45 · 买家提交（案例 A）

**操作：** 在杯子订单上点 *Report a problem*，拖入破损照片，输入 “The handle snapped off when I unpacked it.”，点 *Submit claim*。

> The buyer picks the order, adds a photo and one sentence. That's it, no forms.

**要点：** 入口简单。镜头扫过订单卡上的 “refundable” 金额。

### 0:45 – 1:10 · AI 只描述证据

**画面：** 申请详情页，Evidence 卡填出来后停 2 秒，鼠标依次指 Product match、Damage type、Evidence quality，最后停在 Limitations。

> A vision model compares the buyer's photo with the listing photo. It reports facts: same product, broken, evidence sufficient. And it says what it can't know, like when the damage happened. Notice what's missing: the AI never outputs an amount. Its schema simply has no field for money, so a prompt injection in the buyer's text can't change the payout.

**要点：** AI 有用且诚实；职责边界清晰。

### 1:10 – 1:28 · 规则决定金额

**画面：** Decision 卡，指向 Rule `R1-broken`、金额、`policy standard-1.0`。

> The amount comes from the merchant's own policy: deterministic, versioned rules. Rule R1 matched, full refund. The decision records the exact policy version it used.

**要点：** 可解释、可审计。

### 1:28 – 1:58 · 商家批准，PayPal 执行

**操作：** 切到 `/merchant`，指一下顶部统计卡，点开这条申请，抽屉里确认金额，点 *Approve & refund via PayPal*，确认弹窗。状态变 Completed 后停住，指 refund id。可选：切到 PayPal 沙盒后台标签，展示同一笔退款。

> The merchant sees a queue with the AI facts and the policy outcome side by side. One click approves. RefundPilot calls the PayPal refund API on the original capture, with an idempotency key, so a double click or a network timeout can never refund twice. It only shows "refunded" once PayPal reports the refund as completed.

**要点：** PayPal 是真执行，不是装饰；幂等是技术深度的证据。

### 1:58 – 2:22 · 知道什么时候不动钱（案例 B）

**操作：** 提前提交好案例 B（节省时间），直接在队列里打开它。指 Product match = Mismatch、Rule `R4-mismatch`、Request evidence。

> Here the photo shows a different product. The evidence doesn't match the order, so the policy asks for more evidence instead of paying. No money moved.

**要点：** 系统知道何时停手，评委最关心这一点。

### 2:22 – 2:38 · 政策模拟器

**操作：** 打开 `/merchant/policy`，点 *Cosmetic scratch* 预设，三列金额同时变化。

> Merchants can see how a policy behaves before they adopt it. The same cosmetic scratch: fifty percent under Goodwill, thirty under Standard, fifteen under Strict.

**要点：** 分层架构的价值，产品完整度。

### 2:38 – 2:48 · 收尾

**画面：** 回到案例 A 的 Audit trail，从上到下滚一遍，然后切到标题卡。

> Every step, buyer, AI, rules, merchant, PayPal, is on the record. RefundPilot: AI reads the evidence, policy decides, a human approves, PayPal pays.

## 开拍前检查

- [ ] 线上 `/health` 显示真实 PayPal 网关和 `chain`（不是 `fake`）
- [ ] 商品图已换成真实照片，案例 A、B 各在本地试跑过一次，结果符合预期
- [ ] 案例 B 已提前提交并完成评估
- [ ] 案例 A 用的订单剩余可退金额等于订单金额
- [ ] Render 两个服务刚访问过（避免首个请求慢）
- [ ] 录两遍，取好的；剪掉等待 AI 的空白（保留状态变化的瞬间）
- [ ] 上传 YouTube 设为 Public，标题含 “RefundPilot”，不用版权音乐，不出现第三方商标
