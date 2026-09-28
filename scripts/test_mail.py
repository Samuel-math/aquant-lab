#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aquant.mailer import deliver
result=deliver('setup-test-v1','AQuant Lab 邮件配置测试','这是模拟组合通知的测试邮件，不包含实际交易指令。','/root/autodl-tmp/aquant/mail.sqlite')
print(result)
raise SystemExit(0 if result['status']=='sent_or_already_sent' else 1)
