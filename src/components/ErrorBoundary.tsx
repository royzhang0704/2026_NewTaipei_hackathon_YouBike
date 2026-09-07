import { Component, type ErrorInfo, type ReactNode } from 'react'

/* render 期例外的最後防線（react-query 的資料錯誤走 isError、不會到這）。
   沒有它，一個 render bug 就整頁白屏。 */

interface Props {
  children: ReactNode
  /** 自訂錯誤畫面（例：塞在側欄裡的小區塊，不要用整頁置中版） */
  fallback?: ReactNode
}
interface State {
  err: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null }

  static getDerivedStateFromError(err: Error): State {
    return { err }
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', err, info.componentStack)
  }

  render() {
    const { err } = this.state
    if (!err) return this.props.children
    if (this.props.fallback !== undefined) return this.props.fallback
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-[0.9rem] font-medium text-ink">畫面發生錯誤</p>
        <p className="max-w-[44ch] break-words text-[0.75rem] leading-[1.6] text-ink3">
          {err.message || '未知錯誤'}
        </p>
        <button
          type="button"
          onClick={() => location.reload()}
          className="mt-1 rounded-xs border border-edge px-3 py-[6px] text-[0.8rem] text-ink hover:bg-raise"
        >
          重新整理
        </button>
      </div>
    )
  }
}
