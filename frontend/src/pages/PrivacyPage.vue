<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

const { t } = useI18n()

const sections = [
  { title: 'privacy.s1Title', body: 'privacy.s1Body' },
  { title: 'privacy.s2Title', body: 'privacy.s2Body' },
  { title: 'privacy.s3Title', body: 'privacy.s3Body' },
  { title: 'privacy.s4Title', body: 'privacy.s4Body' },
  { title: 'privacy.s5Title', body: 'privacy.s5Body' },
  { title: 'privacy.s6Title', body: 'privacy.s6Body' },
] as const
</script>

<template>
  <div class="legal-page">
    <article class="legal-card card-solid">
      <header class="legal-header">
        <h1>{{ t('legal.privacyTitle') }}</h1>
        <p class="meta">{{ t('legal.lastUpdated') }}</p>
      </header>

      <section v-for="s in sections" :key="s.title" class="legal-section">
        <h2>{{ t(s.title) }}</h2>
        <p>{{ t(s.body) }}</p>
      </section>

      <RouterLink to="/" class="btn btn-outline">{{ t('legal.backHome') }}</RouterLink>
    </article>
  </div>
</template>

<style scoped>
.legal-page {
  max-width: 760px;
  margin: 0 auto;
  padding: 40px 20px 64px;
}

.legal-card {
  padding: 40px 36px;
}

.legal-header {
  margin-bottom: 32px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}

.legal-header h1 {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.03em;
  margin-bottom: 8px;
}

.meta {
  font-size: 13px;
  color: var(--muted);
}

.legal-section {
  margin-bottom: 24px;
}

.legal-section h2 {
  font-size: 15px;
  font-weight: 650;
  margin-bottom: 8px;
  color: var(--text);
}

.legal-section p {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-secondary);
}
</style>
