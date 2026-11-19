ROLES = ('admin', 'operator', 'viewer')
RUN_STATES = ('queued', 'running', 'completed', 'failed')
# 签收单核销状态：pending 待核（含部分核销），verified 已核
SIGNOFF_STATES = ('pending', 'verified')
# 对账任务状态
RECON_STATES = ('queued', 'running', 'done', 'failed')
# 待核原因（稳定代码，展示文案在 labels 中维护）
PENDING_REASONS = ('application_missing', 'queued', 'quota_insufficient')
PENDING_REASON_LABELS = {
    'application_missing': '移交申请尚未到达，申请补齐后自动核销',
    'queued': '等待对账调度',
    'quota_insufficient': '申请额度不足，腾出额度后自动重算',
}
