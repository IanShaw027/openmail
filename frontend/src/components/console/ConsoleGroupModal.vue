<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { MailGroup } from '@/utils/groups'

const show = defineModel<boolean>('show', { required: true })
const newGroupName = defineModel<string>('newGroupName', { required: true })
const editingGroupId = defineModel<string | null>('editingGroupId', { required: true })
const editingGroupName = defineModel<string>('editingGroupName', { required: true })

defineProps<{
  groups: MailGroup[]
  groupStats: (id: string) => { total: number; error: number; unknown: number; ok: number }
}>()

const emit = defineEmits<{
  add: []
  startRename: [g: MailGroup]
  saveRename: []
  remove: [id: string]
}>()

const { t } = useI18n()
</script>

<template>
  <div v-if="show" class="modal-backdrop" @click.self="show = false">
    <div class="modal card-solid import-help-modal group-manage-modal">
      <header class="modal-head">
        <h2>{{ t('console.groupManage') }}</h2>
        <button type="button" class="btn btn-ghost btn-sm" @click="show = false">
          {{ t('common.close') }}
        </button>
      </header>
      <div class="modal-body">
        <ul class="group-list">
          <li v-for="g in groups" :key="g.id" class="group-list-item">
            <div class="group-main">
              <template v-if="editingGroupId === g.id">
                <input
                  v-model="editingGroupName"
                  class="input group-rename-input"
                  :placeholder="t('console.groupRenamePh')"
                  @keydown.enter.prevent="emit('saveRename')"
                  @keydown.esc.prevent="editingGroupId = null"
                />
                <button type="button" class="btn btn-primary btn-xs" @click="emit('saveRename')">
                  {{ t('common.save') }}
                </button>
                <button type="button" class="btn btn-ghost btn-xs" @click="editingGroupId = null">
                  {{ t('common.cancel') }}
                </button>
              </template>
              <template v-else>
                <span class="group-name">{{ g.name }}</span>
                <span class="group-stats muted">{{
                  t('console.groupStats', groupStats(g.id))
                }}</span>
              </template>
            </div>
            <div v-if="editingGroupId !== g.id" class="group-acts">
              <button type="button" class="btn btn-ghost btn-xs" @click="emit('startRename', g)">
                {{ t('console.groupRename') }}
              </button>
              <button
                v-if="g.id !== 'default'"
                type="button"
                class="btn btn-ghost btn-xs act-del"
                @click="emit('remove', g.id)"
              >
                {{ t('common.delete') }}
              </button>
            </div>
          </li>
        </ul>
        <div class="btn-row group-add-row">
          <input
            v-model="newGroupName"
            class="input"
            :placeholder="t('console.groupNewName')"
            @keydown.enter.prevent="emit('add')"
          />
          <button type="button" class="btn btn-primary btn-sm" @click="emit('add')">
            {{ t('console.groupNew') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
