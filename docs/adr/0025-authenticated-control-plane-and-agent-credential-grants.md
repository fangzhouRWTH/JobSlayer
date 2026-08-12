# ADR-0025：认证控制面与短期 Agent 凭据授权

- 状态：Accepted
- 日期：2026-08-12

## 背景

早期 `actor_id`/`authorized_by` 只是声明字符串，不能证明调用者身份，也不能阻止伪造
authority。控制面写操作必须在副作用前认证、授权并保留主体证据，同时不能把 JobSlayer
签名密钥或长期操作员凭据交给 Agent。

## 决定

1. 领域层只保留认证主体、授权请求/结论、可验证 proof 和 credential grant 契约；OIDC、
   mTLS、本地 HMAC 或具体 Agent SDK 不进入 `jobslayer.domain`。
2. 本地开发 adapter 使用 create-only 0600 HMAC key，签发最长 24 小时的 session；RBAC
   默认拒绝。公共 CLI/UI 写操作都要求 session/key，并在任何状态或 Git 副作用前校验。
3. execution authority 与 approval authority 都由签名 proof 绑定 issuer、session、policy、
   action、主体及有效期；execution authority 还绑定 task/run。篡改、过期、错角色、错
   task/run 和无签名 authority 全部拒绝。
4. `AgentCredentialBroker` 只向控制面返回不含秘密的短期 grant 证据。治理执行器要求
   delegate 声明它实际绑定了同一 grant，并在终态后精确 revoke；JobSlayer 身份签名 key
   永不进入 Agent 环境、日志或制品。
5. 当前仓库不内置长期模型秘密或假装本地 HMAC 是生产 IdP。真实 Codex 凭据必须由后续
   部署 adapter 短期注入；没有该 adapter 时，强治理路径默认拒绝真实外部模型执行。

## 后果

- 公共写入口不再接受自由文本身份；旧 `authorized_by` 只保留给内部 Phase 0 兼容测试。
- 越权在调用 executor、Git 或状态事务前失败，并有正反测试。
- OIDC/mTLS、撤销列表和生产 secret broker 可替换 adapter，不改变工作流或领域契约。

