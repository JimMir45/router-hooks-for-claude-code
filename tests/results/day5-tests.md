# Day 5 测试结果

- ④.1 长 task 进度可见: **通过**
- ④.2 sub-agent 失败回退: **通过**
- ⑦ uninstall < 10s: **通过** (实际 0.110s)

## 备注
- ④.1 验的是 `[PROGRESS] phase=N/total` 在 sub_prompt 必约定 + dispatch text 告诉主 session 转发
- ④.2 验的是 `[OUTCOME status=success|failed]` envelope + supervisor 协议含 fallback 路径
- ⑦ 在 temp 拷贝里跑,不动真实 hook/。回滚后 router.py 必须语法对 + 无 Director-Worker 残留