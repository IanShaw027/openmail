<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const show = defineModel<boolean>('show', { required: true })
const to = defineModel<string>('to', { required: true })
const subject = defineModel<string>('subject', { required: true })
const body = defineModel<string>('body', { required: true })

defineProps<{
  email?: string
  busy?: boolean
}>()

const emit = defineEmits<{
  send: []
}>()

const { t } = useI18n()
</script>

<template>
  <div v-if="show" class="modal-backdrop" @click.self="show = false">
    <div class="modal card-solid import-help-modal">
      <header class="modal-head">
        <h2>{{ t('console.sendTitle') }} · {{ email }}</h2>
        <button type="button" class="btn btn-ghost btn-sm" @click="show = false">
          {{ t('common.close') }}
        </button>
      </header>
      <div class="modal-body">
        <div class="field">
          <label class="label">{{ t('console.sendTo') }}</label>
          <input v-model="to" class="input" type="text" placeholder="a@b.com, c@d.com" />
        </div>
        <div class="field">
          <label class="label">{{ t('console.sendSubject') }}</label>
          <input v-model="subject" class="input" type="text" />
        </div>
        <div class="field">
          <label class="label">{{ t('console.sendBody') }}</label>
          <textarea v-model="body" class="textarea" rows="8" />
        </div>
        <div class="btn-row">
          <button type="button" class="btn btn-primary" :disabled="busy" @click="emit('send')">
            {{ busy ? t('common.loading') : t('console.sendAction') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
