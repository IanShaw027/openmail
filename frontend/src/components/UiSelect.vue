<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'

export interface UiSelectOption {
  value: string | number
  label: string
  title?: string
  disabled?: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue: string | number | null | undefined
    options: UiSelectOption[]
    placeholder?: string
    disabled?: boolean
    size?: 'sm' | 'md'
    block?: boolean
    mono?: boolean
    title?: string
  }>(),
  {
    placeholder: '—',
    disabled: false,
    size: 'md',
    block: true,
    mono: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string | number]
}>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const listEl = ref<HTMLElement | null>(null)
const highlight = ref(-1)
/** Fixed-position menu so parent overflow:hidden (pager/table) cannot clip options */
const menuStyle = ref<Record<string, string>>({})

const selected = computed(() =>
  props.options.find((o) => String(o.value) === String(props.modelValue ?? '')),
)

const display = computed(() => selected.value?.label ?? props.placeholder)

function placeMenu() {
  const el = root.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const spaceBelow = window.innerHeight - r.bottom
  const openUp = spaceBelow < 200 && r.top > spaceBelow
  const width = Math.max(r.width, 64)
  menuStyle.value = {
    position: 'fixed',
    left: `${Math.max(4, Math.min(r.left, window.innerWidth - width - 4))}px`,
    width: `${width}px`,
    minWidth: `${width}px`,
    zIndex: 'var(--z-dropdown, 140)',
    ...(openUp
      ? { bottom: `${window.innerHeight - r.top + 4}px`, top: 'auto' }
      : { top: `${r.bottom + 4}px`, bottom: 'auto' }),
  }
}

function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    const idx = props.options.findIndex(
      (o) => String(o.value) === String(props.modelValue ?? ''),
    )
    highlight.value = idx >= 0 ? idx : 0
    placeMenu()
    void nextTick(() => scrollHighlight())
  }
}

function close() {
  open.value = false
  highlight.value = -1
}

function onWinChange() {
  if (open.value) placeMenu()
}

function pick(opt: UiSelectOption) {
  if (opt.disabled) return
  emit('update:modelValue', opt.value)
  close()
}

function onDocPointer(e: Event) {
  if (!open.value || !root.value) return
  const t = e.target as Node
  // Menu is Teleported to <body>, so it is outside root — still treat it as inside.
  if (root.value.contains(t) || listEl.value?.contains(t)) return
  close()
}

function scrollHighlight() {
  const list = listEl.value
  if (!list || highlight.value < 0) return
  const item = list.children[highlight.value] as HTMLElement | undefined
  item?.scrollIntoView({ block: 'nearest' })
}

function onKey(e: KeyboardEvent) {
  if (props.disabled) return
  if (!open.value) {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
      e.preventDefault()
      open.value = true
      const idx = props.options.findIndex(
        (o) => String(o.value) === String(props.modelValue ?? ''),
      )
      highlight.value = idx >= 0 ? idx : 0
    }
    return
  }
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
    return
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    highlight.value = Math.min(props.options.length - 1, highlight.value + 1)
    void nextTick(scrollHighlight)
    return
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault()
    highlight.value = Math.max(0, highlight.value - 1)
    void nextTick(scrollHighlight)
    return
  }
  if (e.key === 'Enter') {
    e.preventDefault()
    const opt = props.options[highlight.value]
    if (opt) pick(opt)
  }
}

onMounted(() => {
  document.addEventListener('pointerdown', onDocPointer, true)
  window.addEventListener('resize', onWinChange)
  window.addEventListener('scroll', onWinChange, true)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocPointer, true)
  window.removeEventListener('resize', onWinChange)
  window.removeEventListener('scroll', onWinChange, true)
})
</script>

<template>
  <div
    ref="root"
    class="ui-select"
    :class="[
      `size-${size}`,
      {
        open,
        disabled,
        block,
        mono,
        placeholder: !selected,
      },
    ]"
    :title="title || selected?.title || selected?.label || undefined"
  >
    <button
      type="button"
      class="ui-select-trigger"
      :disabled="disabled"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
      @keydown="onKey"
    >
      <span class="ui-select-label">{{ display }}</span>
      <span class="ui-select-chevron" aria-hidden="true">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
          <path
            d="M2.5 4.5L6 8L9.5 4.5"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </span>
    </button>
    <Teleport to="body">
      <ul
        v-if="open"
        ref="listEl"
        class="ui-select-menu"
        role="listbox"
        :style="menuStyle"
      >
        <li
          v-for="(opt, i) in options"
          :key="String(opt.value)"
          role="option"
          class="ui-select-option"
          :class="{
            active: String(opt.value) === String(modelValue ?? ''),
            highlight: i === highlight,
            disabled: opt.disabled,
          }"
          :aria-selected="String(opt.value) === String(modelValue ?? '')"
          :title="opt.title || opt.label"
          @click="pick(opt)"
          @mouseenter="highlight = i"
        >
          {{ opt.label }}
        </li>
        <li v-if="!options.length" class="ui-select-empty">—</li>
      </ul>
    </Teleport>
  </div>
</template>

<style scoped>
/* Aligns with tokens: --control-h / --control-radius / focus ring */
.ui-select {
  position: relative;
  display: inline-flex;
  vertical-align: middle;
  min-width: 0;
  font-size: var(--control-font, 13px);
  color: var(--text);
}
.ui-select.block {
  display: flex;
  width: 100%;
}
.ui-select.disabled {
  opacity: 0.55;
  pointer-events: none;
}
.ui-select.mono .ui-select-label,
.ui-select.mono .ui-select-option {
  font-family: var(--mono);
  font-size: var(--control-font-sm, 12px);
}

.ui-select-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-width: 0;
  height: var(--control-h, 40px);
  min-height: var(--control-h, 40px);
  margin: 0;
  border: 1px solid var(--control-border, var(--border-strong));
  border-radius: var(--control-radius, var(--radius-sm));
  background: var(--bg-elevated);
  color: inherit;
  padding: 0 10px 0 var(--control-pad-x, 12px);
  text-align: left;
  cursor: pointer;
  box-shadow: var(--control-shadow, var(--shadow-sm));
  font-size: inherit;
  font-weight: 500;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease,
    background 0.15s ease;
}
.ui-select-trigger:hover {
  border-color: color-mix(in srgb, var(--accent) 32%, var(--border-strong));
}
.ui-select.open .ui-select-trigger,
.ui-select-trigger:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-ring);
}
.ui-select.placeholder .ui-select-label {
  color: var(--muted);
  font-weight: 400;
}

.ui-select-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: var(--control-line, 1.3);
  padding: 2px 0;
}

.ui-select-chevron {
  flex-shrink: 0;
  display: inline-flex;
  color: var(--muted);
  transition: transform 0.15s ease;
}
.ui-select.open .ui-select-chevron {
  transform: rotate(180deg);
  color: var(--accent);
}

.ui-select-menu {
  /* position/size set via fixed menuStyle (Teleport) */
  box-sizing: border-box;
  max-height: min(280px, 50vh);
  overflow: auto;
  margin: 0;
  padding: 6px;
  list-style: none;
  background: var(--panel-solid);
  border: 1px solid var(--control-border, var(--border-strong));
  border-radius: 12px;
  box-shadow: var(--shadow-lg);
  overscroll-behavior: contain;
  animation: ui-menu-in 0.12s ease;
  color: var(--text);
  font-size: var(--control-font, 13px);
}

.ui-select-option {
  padding: 8px 10px;
  border-radius: 8px;
  line-height: 1.35;
  cursor: pointer;
  color: var(--text);
  word-break: break-word;
  font-size: inherit;
}
.ui-select-option.highlight {
  background: var(--panel-soft);
}
.ui-select-option.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 650;
}
.ui-select-option.disabled {
  opacity: 0.4;
  pointer-events: none;
}
.ui-select-empty {
  padding: 10px;
  color: var(--muted);
  font-size: var(--control-font-sm, 12px);
  text-align: center;
}

.ui-select.size-sm {
  font-size: var(--control-font-sm, 12px);
}
.ui-select.size-sm .ui-select-trigger {
  height: var(--control-h-sm, 32px);
  min-height: var(--control-h-sm, 32px);
  padding: 0 8px 0 10px;
  border-radius: var(--control-radius-sm, 8px);
}
.ui-select.size-sm .ui-select-option {
  padding: 6px 8px;
}
.ui-select.size-sm .ui-select-menu {
  padding: 4px;
  border-radius: 10px;
}

@keyframes ui-menu-in {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
