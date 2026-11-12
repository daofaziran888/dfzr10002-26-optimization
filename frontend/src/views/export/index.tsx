import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/export'
const columns = [
  "模板编号",
  "模板名称",
  "数据源绑定",
  "导出格式",
  "字段选择",
  "筛选参数",
  "导出上限",
  "超限策略",
  "是否启用"
]
const actions = [
  "提交校验",
  "启用配置",
  "停用配置"
]
const statuses = [
  "草稿",
  "待校验",
  "已启用",
  "已停用"
]
const stats = [
  {
    "label": "导出模板总数",
    "value": 0
  },
  {
    "label": "待校验导出模板",
    "value": 0
  },
  {
    "label": "已启用导出模板",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type Preview = {
  模板编号: string
  模板名称: string | null
  模板状态: string
  数据源绑定: string
  数据代际: number
  字段选择: string[]
  导出格式: string
  筛选参数: string
  导出上限: number
  超限策略: string
  结果总数: number
  结果范围: { start: number; end: number }
  导出状态: string
  状态说明: string
  快照键: string
  缓存状态: string
  缓存说明: string
  样例行: Record<string, string | number | null>[]
}

const editFields = ["字段选择", "导出格式", "筛选参数", "导出上限", "超限策略"] as const

export default function ExportView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<Preview | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [editValues, setEditValues] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('导出模板列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据导出配置列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('数据导出配置动作未生效，请稍后重试')
      }
      await reload()
      if (selectedId === Number(row.id)) {
        await showPreview(row)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据导出配置操作失败')
    }
  }

  async function showPreview(row: Row) {
    setErrorMessage('')
    setNotice('')
    setSelectedId(Number(row.id))
    setEditValues({
      字段选择: String(row['字段选择'] ?? ''),
      导出格式: String(row['导出格式'] ?? 'CSV'),
      筛选参数: String(row['筛选参数'] ?? ''),
      导出上限: String(row['导出上限'] ?? '500'),
      超限策略: String(row['超限策略'] ?? '截断'),
    })
    try {
      const response = await request(`${ENDPOINT}/${row.id}/preview`)
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload?.detail ?? '导出预览生成失败')
      }
      setPreview(payload as Preview)
    } catch (error) {
      setPreview(null)
      setErrorMessage(error instanceof Error ? error.message : '导出预览生成失败')
    }
  }

  async function saveConfig() {
    if (selectedId === null) return
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${selectedId}`, {
        method: 'PUT',
        body: JSON.stringify(editValues),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload?.message ?? payload?.detail ?? '模板配置保存失败')
      }
      setNotice('模板配置已保存：旧快照已失效，下一次下载采用新的字段次序')
      await reload()
      const updated = rows.find((row) => Number(row.id) === selectedId)
      if (updated) {
        await showPreview({ ...updated, ...editValues })
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '模板配置保存失败')
    }
  }

  async function download(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/download`)
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        throw new Error(payload?.detail ?? '导出文件生成失败')
      }
      const blob = await response.blob()
      const disposition = response.headers.get('Content-Disposition') ?? ''
      const match = /filename\*=UTF-8''([^;]+)/.exec(disposition)
      const filename = match ? decodeURIComponent(match[1]) : `${row['模板编号'] ?? 'export'}.csv`
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      URL.revokeObjectURL(url)
      const cache = response.headers.get('X-Snapshot-Cache')
      const key = response.headers.get('X-Snapshot-Key')
      const statusRaw = response.headers.get('X-Export-Status') ?? ''
      const status = statusRaw ? decodeURIComponent(statusRaw) : ''
      setNotice(
        `文件已生成：${filename}（快照键 ${key}，缓存${cache === 'HIT' ? '命中，复用同一快照' : '未命中，新建快照'}，导出状态：${status}）`
      )
      if (selectedId === Number(row.id)) {
        await showPreview(row)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '导出文件生成失败')
    }
  }

  return (
    <section className="page" data-module="export">
      <header className="page-head">
        <div>
          <h2>数据导出配置管理</h2>
          <p className="page-desc">维护分析结果的导出模板、格式配置与行数控制，实现分析数据灵活输出。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('导出模板登记入口尚未接入审批流')}>
            登记导出模板
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出数据导出配置清单
          </button>
        </div>
      </header>

      <p className="note-text">
        超限策略说明：结果行数达到导出上限时，「截断」只导出前 N 行并在预览中标注，「拒绝」不生成文件、下载返回提示；
        策略与字段次序都会写入快照键，界面状态与文件内容保持一致。
      </p>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)} className={selectedId === Number(row.id) ? 'row-selected' : ''}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => void showPreview(row)}>
                  结果预览
                </button>
                <button className="link" type="button" onClick={() => void download(row)}>
                  下载文件
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无数据导出配置数据，可先登记导出模板</td>
            </tr>
          )}
        </tbody>
      </table>

      {preview && (
        <section className="preview-panel">
          <h3>导出结果预览 · {preview.模板编号}（{preview.模板名称}）</h3>
          <div className="preview-grid">
            <div className="preview-item">
              <span>字段选择（有序）</span>
              <strong>{preview.字段选择.join(' → ')}</strong>
            </div>
            <div className="preview-item">
              <span>导出格式</span>
              <strong>{preview.导出格式}</strong>
            </div>
            <div className="preview-item">
              <span>结果范围</span>
              <strong>
                {preview.结果总数 === 0
                  ? '无数据（第 0–0 行）'
                  : `第 ${preview.结果范围.start}–${preview.结果范围.end} 行 / 共 ${preview.结果总数} 行`}
              </strong>
            </div>
            <div className="preview-item">
              <span>导出上限</span>
              <strong>{preview.导出上限} 行</strong>
            </div>
            <div className="preview-item">
              <span>超限策略</span>
              <strong>{preview.超限策略}</strong>
            </div>
            <div className="preview-item">
              <span>导出状态</span>
              <strong className={preview.导出状态 === '完整' ? 'tag ok' : 'tag warn'}>{preview.导出状态}</strong>
            </div>
            <div className="preview-item">
              <span>数据代际</span>
              <strong>第 {preview.数据代际} 代</strong>
            </div>
            <div className="preview-item">
              <span>当前缓存快照</span>
              <strong className={preview.缓存状态 === '命中' ? 'tag ok' : 'tag miss'}>
                {preview.缓存状态 === '命中' ? '命中' : '未命中'}
              </strong>
            </div>
          </div>
          <p className="note-text">{preview.状态说明}；快照键 {preview.快照键}，{preview.缓存说明}。</p>

          {preview.样例行.length > 0 && (
            <table className="data-table sample-table">
              <thead>
                <tr>{preview.字段选择.map((field) => <th key={field}>{field}</th>)}</tr>
              </thead>
              <tbody>
                {preview.样例行.map((sample, index) => (
                  <tr key={index}>
                    {preview.字段选择.map((field) => (
                      <td key={field}>{sample[field] === null || sample[field] === undefined || sample[field] === '' ? '（空）' : String(sample[field])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <form
            className="filter-bar"
            onSubmit={(event) => {
              event.preventDefault()
              void saveConfig()
            }}
          >
            {editFields.map((field) => (
              <label key={field} className="filter-item">
                <span>{field}{field === '字段选择' ? '（逗号分隔，顺序写入快照键）' : ''}</span>
                {field === '导出格式' || field === '超限策略' ? (
                  <select
                    value={editValues[field] ?? ''}
                    onChange={(event) => setEditValues((prev) => ({ ...prev, [field]: event.target.value }))}
                  >
                    {(field === '导出格式' ? ['CSV', 'JSON'] : ['截断', '拒绝']).map((option) => (
                      <option key={option} value={option}>{option}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={editValues[field] ?? ''}
                    placeholder={field === '筛选参数' ? '形如 status=已启用，留空不筛选' : ''}
                    onChange={(event) => setEditValues((prev) => ({ ...prev, [field]: event.target.value }))}
                  />
                )}
              </label>
            ))}
            <button className="btn" type="submit">保存模板配置</button>
          </form>
        </section>
      )}

      <footer className="page-foot">
        <span>共 {total} 条数据导出配置记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
