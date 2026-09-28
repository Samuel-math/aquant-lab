"""Private JSON SMTP configuration; no secrets in CLI arguments or diagnostics."""
import json
import os
import stat
from pathlib import Path
from .core import ValidationError
from .report import send_text_mail


def deliver(report_id, subject, body, state_path, config_path=None):
    path=Path(config_path or os.environ.get('AQUANT_MAIL_CONFIG','/root/.config/aquant/mail.json'))
    if not path.exists(): return {'status':'not_configured'}
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValidationError('邮件配置必须仅文件所有者可读写（chmod 600）')
    cfg=json.loads(path.read_text())
    required=('SMTP_HOST','SMTP_PORT','SMTP_USER','SMTP_PASSWORD','MAIL_FROM','MAIL_TO')
    if any(not cfg.get(k) for k in required): return {'status':'not_configured'}
    previous={k:os.environ.get(k) for k in required}
    try:
        for k in required: os.environ[k]=str(cfg[k])
        send_text_mail(report_id,subject,body,state_path)
        return {'status':'sent_or_already_sent'}
    except Exception as e:
        # SMTP replies may echo account information; keep diagnostics bounded.
        return {'status':'needs_review','error_type':type(e).__name__}
    finally:
        for k,value in previous.items():
            if value is None: os.environ.pop(k,None)
            else: os.environ[k]=value
