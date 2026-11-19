import time,traceback,json,os
from pathlib import Path
from src.config.database import Session
from src.models.entities import Trial
from src.services.trials import queue
from src.services.concurrency import execute
from src.services import reconcile
ARTIFACT_DIR = Path(os.getenv('ARTIFACT_DIR', '/artifacts'))
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
# 启动自愈：上一任 worker 若崩溃，遗留的 running 对账任务重置为 queued 续跑。
with Session() as db:
    recovered = reconcile.reset_running(db)
if recovered:
    print(f'reconcile: recovered {recovered} stale running job(s)')
while True:
    Path('/tmp/worker-heartbeat').touch()
    # 签收对账：处理排队任务（无任务时立即返回，不阻塞锁演练队列）。
    try:
        reconcile.run_available(limit=10)
    except Exception:
        traceback.print_exc()
    item=queue.blpop('locklab:queue',timeout=2)
    if not item: continue
    ident=int(item[1])
    try:
        result=execute(ident)
        (ARTIFACT_DIR / f'trial-{ident}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as e:
        traceback.print_exc()
        with Session() as db:
            obj=db.get(Trial,ident)
            if obj: obj.status='failed';obj.error=str(e);db.commit()
