<script setup lang="ts">
import { computed } from 'vue'
import type { Level } from '../api'
import { level } from '../labels'

const props = defineProps<{ level: Level }>()
const l = computed(() => level(props.level))
</script>

<template>
  <a-tag :color="l.color">{{ l.text }}</a-tag>
</template>
