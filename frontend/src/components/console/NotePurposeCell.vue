<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { MailAccount } from '@/types/account'
import {
  type PurposeDef,
  addCustomPurpose,
  hasPurposeToken,
  isPurposeActive,
  isPurposeInactive,
  loadPurposeCatalog,
  noteDisplayText,
  purposeAccent,
  purposeSvgParts,
  togglePurposeInNote,
} from '@/utils/notePurpose'

const props = defineProps<{
  account: MailAccount
}>()

const emit = defineEmits<{
  patch: [note: string]
}>()

const { t } = useI18n()
const catalog = ref<PurposeDef[]>(loadPurposeCatalog())
const open = ref(false)
const root = ref<HTMLElement | null>(null)
const panelEl = ref<HTMLElement | null>(null)
const customLabel = ref('')
const freeText = ref('')
/** Fixed position so table overflow cannot clip the panel */
const panelStyle = ref<Record<string, string>>({})

const display = computed(() => noteDisplayText(props.account.note) || t('console.noteEmpty'))
const hasNote = computed(() => !!noteDisplayText(props.account.note))

/** Visible tokens for list strip (active + inactive, not plain free text). */
const stripPurposes = computed(() =>
  catalog.value.filter((x) => hasPurposeToken(props.account.note, x.key)),
)

function labelFor(p: PurposeDef) {
  return p.label
}

function stateClass(p: PurposeDef) {
  if (isPurposeActive(props.account.note, p.key)) return 'on'
  if (isPurposeInactive(props.account.note, p.key)) return 'off'
  return 'idle'
}

function chipAccent(p: PurposeDef): string {
  return purposeAccent(p.key) || p.color
}

/** Sync free-text field from current note (active + plain only; inactive shown as ~~label~~). */
function syncFreeTextFromNote(note?: string | null) {
  freeText.value = formatEditableNote(note)
}

/**
 * Editable line: active/plain labels; inactive as ~~label~~ markdown strike.
 * Lets user see used-but-disabled services and edit freely.
 */
function formatEditableNote(note?: string | null): string {
  if (!note?.trim()) return ''
  return note
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((raw) => {
      if (raw.startsWith('!') && raw.length > 1) {
        const key = raw.slice(1).toLowerCase()
        const p = catalog.value.find((x) => x.key === key)
        return `~~${p?.label || key}~~`
      }
      const key = raw.toLowerCase()
      const p = catalog.value.find((x) => x.key === key)
      return p?.label || raw
    })
    .join(' ')
}

function onToggle(p: PurposeDef) {
  const next = togglePurposeInNote(props.account.note, p.key)
  emit('patch', next)
  // Keep the open panel text in sync immediately (don't wait for prop round-trip)
  if (open.value) syncFreeTextFromNote(next)
}

function placePanel() {
  const el = root.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const width = Math.min(340, Math.max(280, Math.min(window.innerWidth - 16, 340)))
  let left = r.left
  if (left + width > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - width - 8)
  }
  if (left < 8) left = 8

  const spaceBelow = window.innerHeight - r.bottom
  const openUp = spaceBelow < 320 && r.top > spaceBelow
  const maxH = Math.min(460, Math.max(220, (openUp ? r.top : spaceBelow) - 16))

  panelStyle.value = {
    position: 'fixed',
    left: `${left}px`,
    width: `${width}px`,
    zIndex: 'var(--z-dropdown, 140)',
    maxHeight: `${maxH}px`,
    ...(openUp
      ? { bottom: `${window.innerHeight - r.top + 4}px`, top: 'auto' }
      : { top: `${r.bottom + 4}px`, bottom: 'auto' }),
  }
}

function openPanel() {
  open.value = true
  catalog.value = loadPurposeCatalog()
  syncFreeTextFromNote(props.account.note)
  placePanel()
  void nextTick(placePanel)
}

function close() {
  open.value = false
}

function saveFreeText() {
  /**
   * Parse free text:
   * - ~~label~~ or ~~key~~ → inactive purpose token
   * - known purpose label/key → active
   * - otherwise plain free word
   */
  const parts = freeText.value.trim().split(/\s+/).filter(Boolean)
  const out: string[] = []
  for (const part of parts) {
    const strike = part.match(/^~~(.+?)~~$/)
    if (strike) {
      const inner = strike[1]!.trim()
      const byLabel = catalog.value.find(
        (p) => p.label === inner || p.key === inner.toLowerCase(),
      )
      out.push(`!${(byLabel?.key || inner).toLowerCase().replace(/\s+/g, '-')}`)
      continue
    }
    const cleaned = part.replace(/^!/, '')
    const byLabel = catalog.value.find(
      (p) => p.label === cleaned || p.key === cleaned.toLowerCase(),
    )
    if (byLabel) out.push(byLabel.key)
    else out.push(cleaned)
  }
  emit('patch', out.join(' ').trim())
  close()
}

function addCustom() {
  catalog.value = addCustomPurpose(customLabel.value)
  customLabel.value = ''
}

function onDoc(e: Event) {
  if (!open.value || !root.value) return
  const t = e.target as Node
  if (root.value.contains(t) || panelEl.value?.contains(t)) return
  close()
}

function onWinChange() {
  if (open.value) placePanel()
}

/** When account note changes from outside while panel open, resync. */
watch(
  () => props.account.note,
  (n) => {
    if (open.value) syncFreeTextFromNote(n)
  },
)

onMounted(() => {
  document.addEventListener('pointerdown', onDoc, true)
  window.addEventListener('resize', onWinChange)
  window.addEventListener('scroll', onWinChange, true)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', onDoc, true)
  window.removeEventListener('resize', onWinChange)
  window.removeEventListener('scroll', onWinChange, true)
})
</script>

<template>
  <div ref="root" class="npc" @click.stop>
    <button
      type="button"
      class="npc-display"
      :class="{ empty: !hasNote }"
      :title="t('console.noteClickEdit')"
      @click="openPanel"
    >
      <span class="npc-text">{{ display }}</span>
    </button>

    <!-- Compact strip: only tokens already present on this account -->
    <div v-if="!open && stripPurposes.length" class="npc-strip">
      <button
        v-for="p in stripPurposes"
        :key="p.key"
        type="button"
        class="npc-icon"
        :class="[stateClass(p), { brand: p.kind === 'brand' }]"
        :style="{ '--npc-c': chipAccent(p) }"
        :title="labelFor(p)"
        @click="onToggle(p)"
      >
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path
            v-for="(part, i) in purposeSvgParts(p.key)"
            :key="i"
            :d="part.d"
            fill="currentColor"
            :opacity="part.opacity ?? 1"
          />
        </svg>
      </button>
    </div>

    <Teleport to="body">
      <div
        v-if="open"
        ref="panelEl"
        class="npc-panel"
        :style="panelStyle"
        @click.stop
      >
        <div class="npc-panel-label">{{ t('console.noteTextLabel') }}</div>
        <input
          v-model="freeText"
          class="input input-sm npc-input"
          type="text"
          :placeholder="t('console.notePlaceholder')"
          @keydown.enter="saveFreeText"
          @keydown.escape="close"
        />
        <p class="npc-hint">{{ t('console.notePurposeHint') }}</p>
        <div class="npc-panel-label">{{ t('console.notePurposeLabel') }}</div>
        <div class="npc-icons">
          <button
            v-for="p in catalog"
            :key="p.key"
            type="button"
            class="npc-icon lg"
            :class="[stateClass(p), { brand: p.kind === 'brand' }]"
            :style="{ '--npc-c': chipAccent(p) }"
            :title="`${labelFor(p)} — ${t('console.notePurposeCycle')}`"
            @click="onToggle(p)"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path
                v-for="(part, i) in purposeSvgParts(p.key)"
                :key="i"
                :d="part.d"
                fill="currentColor"
                :opacity="part.opacity ?? 1"
              />
            </svg>
            <span class="npc-tip">{{ labelFor(p) }}</span>
          </button>
        </div>
        <div class="npc-add">
          <input
            v-model="customLabel"
            class="input input-sm"
            type="text"
            :placeholder="t('console.noteAddPreset')"
            @keydown.enter="addCustom"
          />
          <button type="button" class="btn btn-ghost btn-sm" @click="addCustom">
            {{ t('common.add') }}
          </button>
        </div>
        <div class="npc-actions">
          <button type="button" class="btn btn-primary btn-sm" @click="saveFreeText">
            {{ t('common.save') }}
          </button>
          <button type="button" class="btn btn-ghost btn-sm" @click="close">
            {{ t('common.cancel') }}
          </button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.npc {
  position: relative;
  min-width: 0;
}
.npc-display {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  padding: 2px 0;
  cursor: pointer;
  color: inherit;
  font: inherit;
}
.npc-display.empty .npc-text {
  color: var(--muted);
}
.npc-text {
  display: block;
  font-size: 12px;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}
.npc-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.npc-icon {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 7px;
  border: 1px solid var(--border, #e2e8f0);
  background: var(--panel-soft, #f1f5f9);
  color: var(--muted, #64748b);
  cursor: pointer;
  padding: 0;
  transition:
    background 0.12s ease,
    color 0.12s ease,
    border-color 0.12s ease,
    opacity 0.12s ease;
}
.npc-icon.lg {
  width: 36px;
  height: 36px;
  border-radius: 10px;
}
.npc-icon svg {
  display: block;
  flex-shrink: 0;
}
/* Idle: light gray chip, brand-tinted icon */
.npc-icon.idle {
  color: color-mix(in srgb, var(--npc-c, #64748b) 75%, #64748b);
  background: var(--panel-soft, #f1f5f9);
  border-color: var(--border, #e2e8f0);
  opacity: 1;
}
.npc-icon.brand.idle {
  color: var(--npc-c, #64748b);
  background: color-mix(in srgb, var(--npc-c, #6366f1) 10%, #f8fafc);
  border-color: color-mix(in srgb, var(--npc-c, #6366f1) 22%, #e2e8f0);
}
/* Active: solid brand color + white glyph */
.npc-icon.on {
  color: #fff !important;
  background: var(--npc-c, #4f46e5) !important;
  border-color: color-mix(in srgb, var(--npc-c, #4f46e5) 55%, #000) !important;
  box-shadow: 0 1px 3px color-mix(in srgb, var(--npc-c, #4f46e5) 30%, transparent);
  opacity: 1;
}
/* Used/disabled: muted gray, still clickable to remove */
.npc-icon.off {
  color: #94a3b8 !important;
  background: color-mix(in srgb, #94a3b8 14%, #fff) !important;
  border-color: color-mix(in srgb, #94a3b8 35%, #e2e8f0) !important;
  opacity: 0.9;
  box-shadow: none;
}
.npc-icon.idle:hover {
  border-color: var(--npc-c, #4f46e5);
  color: var(--npc-c, #4f46e5);
  background: color-mix(in srgb, var(--npc-c, #4f46e5) 12%, transparent);
}
.npc-tip {
  display: none;
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  white-space: nowrap;
  font-size: 10px;
  font-weight: 650;
  padding: 3px 6px;
  border-radius: 6px;
  background: var(--panel-solid);
  border: 1px solid var(--border);
  color: var(--text);
  box-shadow: var(--shadow-sm);
  z-index: 5;
  pointer-events: none;
}
.npc-icon:hover .npc-tip {
  display: block;
}
</style>

<style>
.npc-panel {
  box-sizing: border-box;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid var(--border-strong, #cbd5e1);
  background: var(--panel-solid, #fff);
  box-shadow: var(--shadow-lg, 0 12px 40px rgba(15, 23, 42, 0.16));
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: auto;
  overscroll-behavior: contain;
}
.npc-panel .npc-panel-label {
  font-size: 10px;
  font-weight: 700;
  color: var(--muted, #64748b);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.npc-panel .npc-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.45;
  color: var(--muted, #64748b);
}
.npc-panel .npc-input {
  width: 100%;
}
.npc-panel .npc-icons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.npc-panel .npc-icon {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  border: 1px solid var(--border, #e2e8f0);
  background: var(--panel-soft, #f1f5f9);
  color: var(--muted, #64748b);
  cursor: pointer;
  padding: 0;
}
.npc-panel .npc-icon.idle {
  color: color-mix(in srgb, var(--npc-c, #64748b) 75%, #64748b);
  background: var(--panel-soft, #f1f5f9);
  border-color: var(--border, #e2e8f0);
}
.npc-panel .npc-icon.brand.idle {
  color: var(--npc-c, #64748b);
  background: color-mix(in srgb, var(--npc-c, #6366f1) 10%, #f8fafc);
  border-color: color-mix(in srgb, var(--npc-c, #6366f1) 22%, #e2e8f0);
}
.npc-panel .npc-icon.on {
  color: #fff !important;
  background: var(--npc-c, #4f46e5) !important;
  border-color: color-mix(in srgb, var(--npc-c, #4f46e5) 55%, #000) !important;
}
.npc-panel .npc-icon.off {
  color: #94a3b8 !important;
  background: color-mix(in srgb, #94a3b8 14%, #fff) !important;
  border-color: color-mix(in srgb, #94a3b8 35%, #e2e8f0) !important;
  opacity: 0.9;
}
.npc-panel .npc-icon.idle:hover {
  border-color: var(--npc-c, #4f46e5);
  color: var(--npc-c, #4f46e5);
}
.npc-panel .npc-tip {
  display: none;
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  white-space: nowrap;
  font-size: 10px;
  font-weight: 650;
  padding: 3px 6px;
  border-radius: 6px;
  background: var(--panel-solid, #fff);
  border: 1px solid var(--border, #e2e8f0);
  color: var(--text, #0f172a);
  z-index: 5;
  pointer-events: none;
}
.npc-panel .npc-icon:hover .npc-tip {
  display: block;
}
.npc-panel .npc-add {
  display: flex;
  gap: 6px;
  align-items: center;
}
.npc-panel .npc-add .input {
  flex: 1;
  min-width: 0;
}
.npc-panel .npc-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}
</style>
