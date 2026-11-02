<script setup lang="ts">
import { computed } from 'vue'
import { eventType } from '../labels'

const props = defineProps<{ type: string }>()
const e = computed(() => eventType(props.type))
</script>

<template>
  <a-tooltip :title="type">
    <a-tag :color="e.color">{{ e.text }}</a-tag>
  </a-tooltip>
</template>
