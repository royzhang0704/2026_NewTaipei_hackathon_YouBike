import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** shadcn 慣例：合併 class，後者覆蓋前者的同類 Tailwind utility。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
