export const ROLES={admin:'管理员',operator:'操作员',viewer:'观察员'};
export const RUN_STATES={queued:'排队中',running:'执行中',completed:'已完成',failed:'失败'};
export const SIGNOFF_STATES={pending:'待核',verified:'已核'};
export const RECON_STATES={queued:'排队中',running:'对账中',done:'已对账',failed:'失败'};
export const PENDING_REASONS={
  application_missing:'移交申请尚未到达，申请补齐后自动核销',
  queued:'等待对账调度',
  quota_insufficient:'申请额度不足，腾出额度后自动重算',
};
