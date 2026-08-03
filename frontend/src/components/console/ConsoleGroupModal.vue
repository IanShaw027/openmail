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
    <div class="modal card-solid group-manage-modal">
      <header class="modal-head">
        <h2>{{ t('console.groupManage') }}</h2>
        <button type="button" class="btn btn-ghost btn-sm" @click="show = false">
          {{ t('common.close') }}
        </button>
      </header>
      <div class="modal-body">
        <ul class="gm-list">
          <li v-for="g in groups" :key="g.id" class="gm-item">
            <div class="gm-main">
              <template v-if="editingGroupId === g.id">
                <div class="gm-rename-row">
                  <input
                    v-model="editingGroupName"
                    class="input"
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
                </div>
              </template>
              <template v-else>
                <div class="gm-title-row">
                  <span class="gm-name">{{ g.name }}</span>
                  <span class="gm-stats muted">{{
                    t('console.groupStats', groupStats(g.id))
                  }}</span>
                </div>
                <div class="gm-acts">
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
              </template>
            </div>
          </li>
        </ul>
        <div class="gm-add">
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

<style scoped>
.group-manage-modal {
  width: min(440px, 100%);
  max-height: min(80vh, 640px);
}
.gm-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.gm-item {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--panel-soft, #f8fafc);
}
.gm-main {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.gm-title-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.gm-name {
  font-weight: 650;
  font-size: 14px;
  color: var(--text);
  line-height: 1.3;
}
.gm-stats {
  font-size: 12px;
  line-height: 1.35;
}
.gm-acts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.gm-rename-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.gm-rename-row .input {
  flex: 1 1 160px;
  min-width: 0;
  height: 32px;
}
.gm-add {
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.gm-add .input {
  flex: 1 1 180px;
  min-width: 0;
}
.act-del {
  color: var(--danger, #dc2626) !important;
}
</style>
