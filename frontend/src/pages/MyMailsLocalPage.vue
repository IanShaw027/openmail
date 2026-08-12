<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMailCacheStore } from '@/stores/mailCache'
import { useAccountsStore } from '@/stores/accounts'
import { copyText } from '@/utils/clipboard'
import { useToast } from '@/composables/useToast'
import { sanitizeHtml } from '@/utils/sanitizeHtml'
import { formatLinkPreview, onEmailHtmlClick } from '@/utils/emailLinks'
import { formatInUserTz, formatInUserTzTitle } from '@/utils/datetime'
import UiSelect, { type UiSelectOption } from '@/components/UiSelect.vue'
import { useSettingsStore } from '@/stores/settings'

const { t, locale } = useI18n()
const mailCache = useMailCacheStore()
const accounts = useAccountsStore()
const settings = useSettingsStore()
const { flashMsg } = useToast()

function formatMailDate(date?: string | null): string {
  void settings.s.timeZone
  return formatInUserTz(date, { locale: locale.value, kind: 'mail' })
}
function formatMailDateTitle(date?: string | null): string {
  void settings.s.timeZone
  return formatInUserTzTitle(date, locale.value)
}

type FolderTab = 'inbox' | 'spam' | 'sent'

const q = ref('')
const from = ref('')
const subject = ref('')
const hasCode = ref(false)
const starredOnly = ref(localStorage.getItem('openmail.myMailsStarredOnly') === '1')
const emailFilter = ref('')
const folder = ref<FolderTab>(
  (localStorage.getItem('openmail.myMailsFolder') as FolderTab) || 'inbox',
)
const selectedKey = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(Number(localStorage.getItem('openmail.myMailsPageSize') || 50) || 50)
const pageSizeOptions = [20, 50, 100, 200]
/** For enter/leave animation direction */
const folderAnim = ref<'left' | 'right'>('right')

const folderTabs: FolderTab[] = ['inbox', 'spam', 'sent']

watch(pageSize, (n) => {
  localStorage.setItem('openmail.myMailsPageSize', String(n))
  page.value = 1
})
watch(starredOnly, (v) => {
  localStorage.setItem('openmail.myMailsStarredOnly', v ? '1' : '0')
  page.value = 1
})
watch(folder, (next, prev) => {
  localStorage.setItem('openmail.myMailsFolder', next)
  const a = folderTabs.indexOf(prev)
  const b = folderTabs.indexOf(next)
  folderAnim.value = b >= a ? 'right' : 'left'
  page.value = 1
  selectedKey.value = null
})

const starredEmails = computed(() =>
  accounts.accounts
    .filter((a) => a.starred && !a.isApiSource)
    .map((a) => a.email.toLowerCase()),
)

const results = computed(() =>
  mailCache.search({
    q: q.value,
    from: from.value,
    subject: subject.value,
    hasCode: hasCode.value,
    email: emailFilter.value || undefined,
    emails: starredOnly.value ? starredEmails.value : undefined,
    folder: folder.value,
  }),
)

/** Raw cache size (all folders) — same store the console writes to. */
const cacheTotal = computed(() => mailCache.totalCount())

const folderCounts = computed(() => {
  const base = {
    q: q.value,
    from: from.value,
    subject: subject.value,
    hasCode: hasCode.value,
    email: emailFilter.value || undefined,
    emails: starredOnly.value ? starredEmails.value : undefined,
  }
  return {
    inbox: mailCache.search({ ...base, folder: 'inbox' }).length,
    spam: mailCache.search({ ...base, folder: 'spam' }).length,
    sent: mailCache.search({ ...base, folder: 'sent' }).length,
  }
})

const totalPages = computed(() => Math.max(1, Math.ceil(results.value.length / pageSize.value)))
const paged = computed(() => {
  const p = Math.min(Math.max(1, page.value), totalPages.value)
  const start = (p - 1) * pageSize.value
  return results.value.slice(start, start + pageSize.value)
})

watch([q, from, subject, hasCode, emailFilter, starredOnly], () => {
  page.value = 1
})
watch(results, () => {
  if (page.value > totalPages.value) page.value = totalPages.value
})

function rowKey(m: { id: string; accountEmail: string }) {
  return `${m.accountEmail}::${m.id}`
}

const selected = computed(() => {
  if (selectedKey.value) {
    const hit = results.value.find((m) => rowKey(m) === selectedKey.value)
    if (hit) return hit
  }
  return paged.value[0] ?? results.value[0] ?? null
})

const detailHtml = computed(() => {
  const m = selected.value
  if (!m?.body_html?.trim()) return ''
  return sanitizeHtml(m.body_html)
})

function onMailHtmlClick(ev: MouseEvent) {
  onEmailHtmlClick(ev, {
    confirmNavigate: (url) =>
      window.confirm(t('console.openLinkConfirm', { url: formatLinkPreview(url), full: url })),
    onBlocked: () => flashMsg(t('console.openLinkBlocked'), 'danger'),
  })
}

const detailText = computed(() => {
  const m = selected.value
  if (!m) return ''
  return m.body_text || m.body_preview || ''
})

const accountEmails = computed(() => {
  const set = new Set(Object.keys(mailCache.byEmail))
  for (const a of accounts.accounts) set.add(a.email.toLowerCase())
  return [...set].sort()
})

const emailSelectOptions = computed<UiSelectOption[]>(() => [
  { value: '', label: t('me.allAccounts') },
  ...accountEmails.value.map((e) => ({ value: e, label: e, title: e })),
])

const pageSizeSelectOptions = computed<UiSelectOption[]>(() =>
  pageSizeOptions.map((n) => ({ value: n, label: String(n) })),
)

function folderLabel(f: FolderTab) {
  if (f === 'spam') return t('console.folderSpam')
  if (f === 'sent') return t('console.folderSent')
  return t('console.folderInbox')
}

async function copyCode(code?: string | null) {
  if (!code) return
  if (await copyText(code)) flashMsg(t('common.copied'))
}
</script>

<template>
  <div class="mails-page">
    <p class="cache-hint muted">
      {{ t('me.sameAsConsole') }}
      · {{ t('me.cacheTotal', { n: cacheTotal }) }}
      <template v-if="results.length !== cacheTotal">
        · {{ t('me.showingFiltered', { n: results.length }) }}
      </template>
    </p>
    <div class="filters card-solid">
      <input v-model="q" class="input" type="search" :placeholder="t('me.filterKeywordPh')" />
      <input v-model="from" class="input" type="search" :placeholder="t('me.filterFromPh')" />
      <input
        v-model="subject"
        class="input"
        type="search"
        :placeholder="t('me.filterSubjectPh') || t('console.mailSubject')"
      />
      <UiSelect
        v-model="emailFilter"
        :options="emailSelectOptions"
        class="email-filter"
        mono
        :title="emailFilter || t('me.allAccounts')"
      />
      <label class="toggle">
        <input v-model="hasCode" type="checkbox" />
        <span class="toggle-track" aria-hidden="true" />
        <span>{{ t('me.filterHasCode') }}</span>
      </label>
      <label class="toggle" :title="t('me.filterStarredHint')">
        <input v-model="starredOnly" type="checkbox" />
        <span class="toggle-track" aria-hidden="true" />
        <span>{{ t('me.filterStarred') }}</span>
      </label>
    </div>
    <p v-if="starredOnly && !starredEmails.length" class="starred-empty hint">
      {{ t('me.filterStarredEmpty') }}
    </p>

    <div class="folder-bar card-solid">
      <div class="mail-tabs" role="tablist">
        <button
          v-for="f in folderTabs"
          :key="f"
          type="button"
          role="tab"
          class="tab"
          :class="{ active: folder === f }"
          :aria-selected="folder === f"
          @click="folder = f"
        >
          {{ folderLabel(f) }}
          <span class="tab-count">{{ folderCounts[f] }}</span>
        </button>
        <span class="tab-indicator" :data-i="folderTabs.indexOf(folder)" aria-hidden="true" />
      </div>
    </div>

    <div class="body">
      <div class="list card-solid">
        <Transition :name="folderAnim === 'right' ? 'slide-r' : 'slide-l'" mode="out-in">
          <div :key="folder" class="list-pane">
            <div v-if="!results.length" class="empty">{{ t('me.noMailsLocal') }}</div>
            <template v-else>
              <button
                v-for="m in paged"
                :key="rowKey(m)"
                type="button"
                class="item"
                :class="{ active: selected && rowKey(selected) === rowKey(m) }"
                @click="selectedKey = rowKey(m)"
              >
                <div class="item-top">
                  <span class="email">{{ m.accountEmail }}</span>
                  <span
                    v-if="m.verification_code"
                    class="code"
                    @click.stop="copyCode(m.verification_code)"
                  >
                    {{ m.verification_code }}
                  </span>
                </div>
                <div class="subj">{{ m.subject || t('console.mailNoSubject') }}</div>
                <div class="meta">
                  {{ m.from || m.from_address }}
                  <template v-if="m.to"> · → {{ m.to }}</template>
                  ·
                  <span :title="formatMailDateTitle(m.date)">{{ formatMailDate(m.date) }}</span>
                </div>
              </button>
            </template>
          </div>
        </Transition>
        <div v-if="results.length" class="pager">
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            :disabled="page <= 1"
            @click="page = Math.max(1, page - 1)"
          >
            {{ t('common.prev') }}
          </button>
          <span class="muted">{{ page }} / {{ totalPages }} · {{ results.length }}</span>
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            :disabled="page >= totalPages"
            @click="page = Math.min(totalPages, page + 1)"
          >
            {{ t('common.next') }}
          </button>
          <UiSelect
            :model-value="pageSize"
            :options="pageSizeSelectOptions"
            class="page-size"
            size="sm"
            :block="false"
            @update:model-value="(v) => (pageSize = Number(v))"
          />
        </div>
      </div>
      <div class="detail card-solid">
        <Transition name="fade" mode="out-in">
          <div v-if="selected" :key="rowKey(selected)" class="detail-inner">
            <h2>{{ selected.subject || t('console.mailNoSubject') }}</h2>
            <p class="meta">
              {{ selected.accountEmail }}
              · {{ selected.from }}
              <template v-if="selected.to"> · → {{ selected.to }}</template>
              ·
              <span :title="formatMailDateTitle(selected.date)">{{
                formatMailDate(selected.date)
              }}</span>
            </p>
            <button
              v-if="selected.verification_code"
              type="button"
              class="btn btn-primary btn-sm"
              @click="copyCode(selected.verification_code)"
            >
              {{ t('console.mailCode') }}: {{ selected.verification_code }}
            </button>
            <div
              v-if="detailHtml"
              class="body-html"
              v-html="detailHtml"
              @click="onMailHtmlClick"
              @auxclick="onMailHtmlClick"
            />
            <pre v-else class="body-text">{{ detailText }}</pre>
          </div>
          <div v-else key="empty" class="empty">{{ t('console.mailDetailEmpty') }}</div>
        </Transition>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mails-page {
  padding: 16px;
  height: calc(100vh - var(--nav-h, 56px));
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-sizing: border-box;
}
.cache-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.4;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 12px;
  align-items: center;
}
.filters .input {
  flex: 1 1 140px;
  max-width: 220px;
  min-width: 0;
}
.filters .email-filter {
  flex: 2 1 280px;
  min-width: min(100%, 260px);
  max-width: min(100%, 480px);
}
.folder-bar {
  padding: 6px 10px;
  flex-shrink: 0;
}
.mail-tabs {
  position: relative;
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  background: var(--panel-soft);
  border-radius: 12px;
  border: 1px solid var(--border);
}
.tab {
  position: relative;
  z-index: 1;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
  padding: 7px 12px;
  border-radius: 9px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: color 0.18s ease;
}
.tab.active {
  color: var(--accent);
}
.tab-count {
  font-size: 10px;
  font-weight: 700;
  min-width: 16px;
  padding: 0 5px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--muted) 14%, transparent);
  color: inherit;
  font-variant-numeric: tabular-nums;
}
.tab.active .tab-count {
  background: var(--accent-soft);
}
.tab-indicator {
  position: absolute;
  z-index: 0;
  top: 3px;
  bottom: 3px;
  width: calc((100% - 6px - 8px) / 3);
  left: 3px;
  border-radius: 9px;
  background: var(--panel-solid);
  box-shadow: var(--shadow-sm);
  border: 1px solid color-mix(in srgb, var(--accent) 22%, var(--border));
  transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}
.tab-indicator[data-i='1'] {
  transform: translateX(calc(100% + 4px));
}
.tab-indicator[data-i='2'] {
  transform: translateX(calc(200% + 8px));
}
.pager .page-size {
  width: 72px;
  flex: 0 0 auto;
}
.body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(260px, 380px) 1fr;
  gap: 12px;
}
.list {
  overflow: hidden;
  padding: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.list-pane {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
}
.item {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  padding: 10px 12px;
  cursor: pointer;
}
.item:hover,
.item.active {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}
.item-top {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
  font-weight: 650;
}
.email {
  color: var(--accent);
}
.code {
  font-family: var(--mono);
  color: var(--accent);
  background: var(--accent-soft);
  padding: 1px 6px;
  border-radius: 6px;
}
.subj {
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
}
.meta {
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}
.pager {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-top: 1px solid var(--border);
  margin-top: auto;
  flex-wrap: wrap;
  font-size: 12px;
  flex-shrink: 0;
  overflow: visible;
  position: relative;
  z-index: 2;
}
.page-size {
  width: 72px;
}
.detail {
  overflow: auto;
  padding: 16px;
  min-height: 0;
}
.detail h2 {
  font-size: 16px;
  margin-bottom: 8px;
}
.body-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.5;
  margin-top: 12px;
  font-family: inherit;
}
.body-html {
  margin-top: 12px;
  font-size: 13px;
  line-height: 1.55;
  word-break: break-word;
  overflow-x: auto;
}
.body-html :deep(img) {
  max-width: 100%;
  height: auto;
}
.body-html :deep(a) {
  color: var(--accent);
}
.empty {
  padding: 32px;
  text-align: center;
  color: var(--muted);
}
.starred-empty {
  margin: -4px 0 0;
  padding: 0 4px;
}

/* Folder switch animations */
.slide-r-enter-active,
.slide-r-leave-active,
.slide-l-enter-active,
.slide-l-leave-active {
  transition:
    opacity 0.22s ease,
    transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}
.slide-r-enter-from {
  opacity: 0;
  transform: translateX(16px);
}
.slide-r-leave-to {
  opacity: 0;
  transform: translateX(-12px);
}
.slide-l-enter-from {
  opacity: 0;
  transform: translateX(-16px);
}
.slide-l-leave-to {
  opacity: 0;
  transform: translateX(12px);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 800px) {
  .body {
    grid-template-columns: 1fr;
  }
  .mail-tabs {
    width: 100%;
  }
  .tab {
    flex: 1;
    justify-content: center;
  }
  .tab-indicator {
    width: calc((100% - 6px - 8px) / 3);
  }
}
</style>
