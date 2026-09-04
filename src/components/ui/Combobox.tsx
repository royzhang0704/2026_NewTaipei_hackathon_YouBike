import { useMemo, useState } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { Command } from 'cmdk'
import { Check, ChevronsUpDown, Search } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ComboItem {
  value: string
  label: string
  /** 分組標題（例：行政區） */
  group?: string
  /** 右側灰字（例：站數 /「代理」） */
  suffix?: string
  /** 額外可搜尋字串（例：UID），不顯示 */
  keys?: string
}

interface Props {
  value: string | null
  onChange: (v: string | null) => void
  items: ComboItem[]
  placeholder?: string
  searchPlaceholder?: string
  disabled?: boolean
}

/** Radix Popover + cmdk 的 Combobox（shadcn 慣用組合）。
    cmdk 自帶過濾：比對 item 的 value + keywords，所以塞 label/區名/UID 進 keywords 即可搜。 */
export function Combobox({
  value,
  onChange,
  items,
  placeholder = '— 請選擇 —',
  searchPlaceholder = '搜尋…',
  disabled,
}: Props) {
  const [open, setOpen] = useState(false)

  const selectedLabel = useMemo(
    () => items.find((i) => i.value === value)?.label ?? '',
    [items, value],
  )

  // 依出現順序切成 [{ group, items }]，同組連續項目合併
  const groups = useMemo(() => {
    const out: { group: string; items: ComboItem[] }[] = []
    for (const it of items) {
      const g = it.group ?? ''
      const last = out[out.length - 1]
      if (last && last.group === g) last.items.push(it)
      else out.push({ group: g, items: [it] })
    }
    return out
  }, [items])

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        disabled={disabled}
        className="flex w-full min-w-[210px] max-w-[330px] items-center justify-between gap-[10px] rounded-xs border border-edge bg-panel px-[10px] py-2 text-left text-[0.86rem] text-ink hover:bg-raise disabled:cursor-default disabled:opacity-40 data-[state=open]:border-ink2"
      >
        <span className={cn('truncate', !selectedLabel && 'text-ink3')}>
          {selectedLabel || placeholder}
        </span>
        <ChevronsUpDown className="size-3 flex-none opacity-45" />
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={4}
          className="z-40 w-[min(340px,86vw)] overflow-hidden rounded-xs border border-edge bg-panel shadow-[0_10px_30px_rgba(0,0,0,.5)]"
        >
          <Command>
            <div className="flex items-center gap-2 border-b border-hair px-[10px] py-2">
              <Search className="size-[13px] flex-none opacity-40" />
              <Command.Input
                placeholder={searchPlaceholder}
                className="min-w-0 flex-1 bg-transparent p-0 text-[0.86rem] outline-none placeholder:text-ink3"
              />
            </div>
            <Command.List className="max-h-[248px] overflow-y-auto p-1">
              <Command.Empty className="px-[10px] py-[18px] text-center text-[0.8rem] text-ink3">
                找不到符合的項目
              </Command.Empty>
              {groups.map((grp) => (
                <Command.Group
                  key={grp.group}
                  heading={grp.group || undefined}
                  className="[&_[cmdk-group-heading]]:sticky [&_[cmdk-group-heading]]:top-0 [&_[cmdk-group-heading]]:bg-panel [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-[3px] [&_[cmdk-group-heading]]:pt-[7px] [&_[cmdk-group-heading]]:text-[0.6rem] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-[0.18em] [&_[cmdk-group-heading]]:text-ink3"
                >
                  {grp.items.map((it) => (
                    <Command.Item
                      key={it.value}
                      value={it.value}
                      keywords={[it.label, it.group, it.keys].filter(Boolean) as string[]}
                      onSelect={() => {
                        onChange(it.value)
                        setOpen(false)
                      }}
                      className="flex cursor-pointer items-center gap-[7px] rounded-xs px-2 py-[6px] text-[0.85rem] leading-[1.35] data-[selected=true]:bg-raise"
                    >
                      <span className="flex size-[13px] flex-none items-center justify-center text-hot">
                        {it.value === value && <Check className="size-[13px]" />}
                      </span>
                      <span className="truncate">{it.label}</span>
                      {it.suffix && (
                        <span className="ml-auto flex-none text-[0.62rem] tracking-[0.1em] text-ink3">
                          {it.suffix}
                        </span>
                      )}
                    </Command.Item>
                  ))}
                </Command.Group>
              ))}
            </Command.List>
          </Command>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
