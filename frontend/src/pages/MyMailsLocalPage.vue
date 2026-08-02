<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMailCacheStore } from '@/stores/mailCache'
import { useAccountsStore } from '@/stores/accounts'
import { copyText } from '@/utils/clipboard'
import { useToast } from '@/composables/useToast'
import { sanitizeHtml } from '@/utils/sanitizeHtml'
import UiSelect, { type UiSelectOption } from '@/components/UiSelect.vue'

const { t } = useI18n()
const mailCache = useMailCacheStore()
const accounts = useAccountsStore()
const { flashMsg } = useToast()

const q = ref('')
const from = ref('')
const subject = ref('')
const hasCode = ref(false)
const starredOnly = ref(localStorage.getItem('openmail.myMailsStarredOnly') === '1')
const emailFilter = ref('')
const selectedKey = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(Number(localStorage.getItem('openmail.myMailsPageSize') || 20) || 20)
const pageSizeOptions = [10, 20, 50]

watch(pageSize, (n) => {
  localStorage.setItem('openmail.myMailsPageSize', String(n))
  page.value = 1
})
watch(starredOnly, (v) => {
  localStorage.setItem('openmail.myMailsStarredOnly', v ? '1' : '0')
  page.value = 1
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
  }),
)

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

async function copyCode(code?: string | null) {
  if (!code) return
  if (await copyText(code)) flashMsg(t('common.copied'))
}
</script>

<template>
  <div class="mails-page">
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

    <div class="body">
      <div class="list card-solid">
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
            <div class="meta">{{ m.from || m.from_address }} · {{ m.date }}</div>
          </button>
          <div class="pager">
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
        </template>
      </div>
      <div class="detail card-solid">
        <template v-if="selected">
          <h2>{{ selected.subject || t('console.mailNoSubject') }}</h2>
          <p class="meta">
            {{ selected.accountEmail }} · {{ selected.from }} · {{ selected.date }}
          </p>
          <button
            v-if="selected.verification_code"
            type="button"
            class="btn btn-primary btn-sm"
            @click="copyCode(selected.verification_code)"
          >
            {{ t('console.mailCode') }}: {{ selected.verification_code }}
          </button>
          <div v-if="detailHtml" class="body-html" v-html="detailHtml" />
          <pre v-else class="body-text">{{ detailText }}</pre>
        </template>
        <div v-else class="empty">{{ t('console.mailDetailEmpty') }}</div>
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
.pager .page-size {
  width: 72px;
}
.body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(260px, 380px) 1fr;
  gap: 12px;
}
.list {
  overflow: auto;
  padding: 0;
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
}
.page-size {
  height: 28px;
  width: 72px;
}
.detail {
  overflow: auto;
  padding: 16px;
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
.tog {
  color: var(--muted);
}
.starred-empty {
  margin: -4px 0 0;
  padding: 0 4px;
}
@media (max-width: 800px) {
  .body {
    grid-template-columns: 1fr;
  }
}
</style>
