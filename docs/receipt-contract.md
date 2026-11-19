# 本地渠道回执

这是独立的演练渠道，不了解居民权益、统计保护、补助资格或预算。内容是否允许投递由调用方判断。服务只保存收到的原始 JSON 及摘要，允许调用方核对已经发生的接收事实。

使用 `python3 -m support.receipt_service --state .local/receipts.sqlite3 --port 8096` 启动，只监听127.0.0.1。端口可修改，状态文件跨服务重启保留。独立试验使用独立状态路径，不能删除正式回执来重置事实。

POST /receipts 接收含 receipt_id 与 payload 的 JSON 对象。receipt_id 是调用方选择的稳定投递身份，payload 是具体内容；首次接收返回201，同身份同内容再次提交返回200，同身份不同内容返回409且不覆盖。响应含 accepted、receipt_id、digest，digest 是按键排序的紧凑 UTF-8 JSON 的 SHA-256。

GET /receipts/{receipt_id} 返回已接收的摘要与内容，未知身份返回404。投递身份应按 URL 路径编码。请求头 X-Reply-Mode: drop_once 可演练接收成功后断开响应：同身份只发生一次，其后查询仍能看到已接收事实。收到断连不能据此认定未接收。服务不计算付款、撤回、补发或披露政策。
