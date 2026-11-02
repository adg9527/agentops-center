<script setup lang="ts">
import zhCN from 'ant-design-vue/es/locale/zh_CN'
</script>

<template>
  <a-config-provider :locale="zhCN">
    <a-app>
      <router-view />
    </a-app>
  </a-config-provider>
</template>
