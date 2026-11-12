import { useEffect, useState } from 'react'

import { fetchJson } from '@/api/client'

type ExportSnapshotSummary = {
  模板编号: string
  快照键: string
  字段选择: string[]
  导出格式: string
  结果范围: { start: number; end: number }
  导出上限: number
  导出状态: string
  缓存状态: string
}

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
  exportSnapshots?: ExportSnapshotSummary[]
}

const fallbackCards = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
const fallbackModules = [{"name": "指标口径管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析数据源", "created": 0, "pending": 0, "abnormal": 0}, {"name": "聚合查询配置", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析实验管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析数据集", "created": 0, "pending": 0, "abnormal": 0}, {"name": "可视化图表配置", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析报告管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据服务发布", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据看板", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析维度管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "全局筛选器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "定时分析任务", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据导出配置", "created": 0, "pending": 0, "abnormal": 0}, {"name": "查询缓存管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析模型管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析操作日志", "created": 0, "pending": 0, "abnormal": 0}]

export default function Overview() {
  const [cards, setCards] = useState<Overview['cards']>(fallbackCards)
  const [moduleRows, setModuleRows] = useState<Overview['modules']>(fallbackModules)
  const [snapshots, setSnapshots] = useState<ExportSnapshotSummary[]>([])

  useEffect(() => {
    fetchJson<Overview>('/api/overview')
      .then((payload) => {
        setCards(payload.cards)
        setModuleRows(payload.modules)
        setSnapshots(payload.exportSnapshots ?? [])
      })
      .catch(() => undefined)
  }, [])

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>运营概览</h2>
          <p className="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
        </div>
      </header>
      <div className="stat-row">
        {cards.map((card) => (
          <article key={card.label} className="stat-card">
            <span className="stat-label">{card.label}</span>
            <strong className="stat-value">{card.value}</strong>
          </article>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
        </thead>
        <tbody>
          {moduleRows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td>{row.created}</td>
              <td>{row.pending}</td>
              <td>{row.abnormal}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <section className="preview-panel">
        <h3>导出快照预览</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>模板编号</th>
              <th>字段选择</th>
              <th>导出格式</th>
              <th>结果范围</th>
              <th>导出上限</th>
              <th>导出状态</th>
              <th>缓存状态</th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((snapshot) => (
              <tr key={snapshot.快照键}>
                <td>{snapshot.模板编号}</td>
                <td>{snapshot.字段选择.join(' → ')}</td>
                <td>{snapshot.导出格式}</td>
                <td>第 {snapshot.结果范围.start}–{snapshot.结果范围.end} 行</td>
                <td>{snapshot.导出上限} 行</td>
                <td>{snapshot.导出状态}</td>
                <td>
                  <strong className={snapshot.缓存状态 === '有效' ? 'tag ok' : 'tag warn'}>
                    {snapshot.缓存状态}
                  </strong>
                </td>
              </tr>
            ))}
            {snapshots.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={7}>暂无导出快照，到「数据导出配置」里执行一次下载即可生成</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </section>
  )
}
