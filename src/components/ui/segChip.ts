import { cn } from '@/lib/utils'

/** 全站統一的「可選片段」外觀：地區 chip、警示篩選、其他 on/off toggle 都走這個。
 *  選中＝ink 實心反白（明確、深淺主題都成立）；未選＝細框弱字。
 *  只管顏色 / 邊框 / 字重 / 圓角；尺寸與 padding 由呼叫端自己加。 */
export function segChip(active: boolean) {
  return cn(
    'rounded-xs border tracking-[0.04em] transition-colors',
    active
      ? 'border-ink bg-ink font-semibold text-bg'
      : 'border-control text-ink2 hover:border-ink3 hover:text-ink',
  )
}
