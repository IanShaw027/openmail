<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
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
  purposeSvgPath,
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
const customLabel = ref('')
const freeText = ref('')

const display = computed(() => noteDisplayText(props.account.note) || t('console.noteEmpty'))
const hasNote = computed(() => !!noteDisplayText(props.account.note))

function labelFor(p: PurposeDef) {
  return p.label
}

function stateClass(p: PurposeDef) {
  if (isPurposeActive(props.account.note, p.key)) return 'on'
  if (isPurposeInactive(props.account.note, p.key)) return 'off'
  return 'idle'
}

function onToggle(p: PurposeDef) {
  const next = togglePurposeInNote(props.account.note, p.key)
  emit('patch', next)
}

function openPanel() {
  open.value = true
  freeText.value = noteDisplayText(props.account.note)
}

function close() {
  open.value = false
}

function saveFreeText() {
  // Keep inactive purpose tokens; replace active/plain display with free text words
  const inactive = (props.account.note || '')
    .split(/\s+/)
    .filter((t) => t.startsWith('!') && t.length > 1)
  const words = freeText.value
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w.replace(/^!/, ''))
  const next = [...inactive, ...words].join(' ').trim()
  emit('patch', next)
  close()
}

function addCustom() {
  catalog.value = addCustomPurpose(customLabel.value)
  customLabel.value = ''
}

function onDoc(e: Event) {
  if (!open.value || !root.value) return
  if (!root.value.contains(e.target as Node)) close()
}

onMounted(() => document.addEventListener('pointerdown', onDoc, true))
onUnmounted(() => document.removeEventListener('pointerdown', onDoc, true))
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
    <div v-if="!open" class="npc-strip">
      <button
        v-for="p in catalog.filter((x) => hasPurposeToken(account.note, x.key))"
        :key="p.key"
        type="button"
        class="npc-icon"
        :class="stateClass(p)"
        :style="{ '--npc-c': p.color }"
        :title="labelFor(p)"
        @click="onToggle(p)"
      >
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path fill="currentColor" :d="purposeSvgPath(p.key)" />
        </svg>
      </button>
    </div>

    <div v-if="open" class="npc-panel">
      <div class="npc-panel-label">{{ t('console.noteTextLabel') }}</div>
      <input
        v-model="freeText"
        class="input input-sm npc-input"
        type="text"
        :placeholder="t('console.notePlaceholder')"
        @keydown.enter="saveFreeText"
        @keydown.escape="close"
      />
      <div class="npc-panel-label">{{ t('console.notePurposeLabel') }}</div>
      <div class="npc-icons">
        <button
          v-for="p in catalog"
          :key="p.key"
          type="button"
          class="npc-icon lg"
          :class="stateClass(p)"
          :style="{ '--npc-c': p.color }"
          :title="`${labelFor(p)} — ${t('console.notePurposeCycle')}`"
          @click="onToggle(p)"
        >
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path fill="currentColor" :d="purposeSvgPath(p.key)" />
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
  border: 1px solid transparent;
  background: var(--panel-soft);
  color: var(--muted);
  cursor: pointer;
  padding: 0;
  transition:
    background 0.15s ease,
    color 0.15s ease,
    border-color 0.15s ease,
    opacity 0.15s ease;
}
.npc-icon.lg {
  width: 34px;
  height: 34px;
  border-radius: 10px;
}
.npc-icon.on {
  color: #fff;
  background: var(--npc-c, var(--accent));
  border-color: color-mix(in srgb, var(--npc-c, var(--accent)) 60%, #000);
  box-shadow: 0 1px 3px color-mix(in srgb, var(--npc-c, var(--accent)) 35%, transparent);
}
.npc-icon.off {
  color: #94a3b8;
  background: color-mix(in srgb, #94a3b8 16%, transparent);
  border-color: color-mix(in srgb, #94a3b8 30%, transparent);
  opacity: 0.85;
}
.npc-icon.idle {
  color: color-mix(in srgb, var(--npc-c, var(--muted)) 70%, var(--muted));
  border-color: var(--border);
}
.npc-icon.idle:hover {
  border-color: var(--npc-c, var(--accent));
  color: var(--npc-c, var(--accent));
  background: color-mix(in srgb, var(--npc-c, var(--accent)) 12%, transparent);
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
.npc-panel {
  position: absolute;
  z-index: 30;
  top: 0;
  left: 0;
  min-width: min(280px, 70vw);
  max-width: 320px;
  padding: 10px;
  border-radius: 12px;
  border: 1px solid var(--border-strong);
  background: var(--panel-solid);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.npc-panel-label {
  font-size: 10px;
  font-weight: 700;
  color: var(--muted);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.npc-input {
  width: 100%;
}
.npc-icons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.npc-add {
  display: flex;
  gap: 6px;
  align-items: center;
}
.npc-add .input {
  flex: 1;
  min-width: 0;
}
.npc-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}
</style>
