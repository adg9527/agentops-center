<script setup lang="ts">
defineProps<{
  phase: number
  title: string
  items: string[]
}>()
</script>

<template>
  <a-card>
    <a-result status="info" :title="`${title}（P${phase} 规划中）`">
      <template #subTitle>该模块将在后续阶段交付，下面是规划内容。</template>
      <template #extra>
        <a-tag color="blue">P{{ phase }}</a-tag>
      </template>
    </a-result>
    <a-list size="small" :data-source="items">
      <template #renderItem="{ item }">
        <a-list-item>
          <a-space><a-tag color="default">规划</a-tag>{{ item }}</a-space>
        </a-list-item>
      </template>
    </a-list>
  </a-card>
</template>
