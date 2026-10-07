<!--
  柑橘合格证追溯系统 —— 根组件

  职责：
    1. 通过 el-config-provider 向全站注入统一的品牌主色（柑橘橙 #E8743B，见 styles/main.css 的 :root）
       与中文语言包，保证未显式指定颜色的 Element Plus 组件也遵循主题色；
    2. 为路由页面切换提供淡入过渡，弱化页面跳转的生硬感；
    3. 页面级布局由 layout/MainLayout.vue 提供，本组件只负责全局配置与路由出口。
-->
<template>
  <el-config-provider :locale="zhCn" size="default">
    <router-view v-slot="{ Component }">
      <transition name="fade-slide" mode="out-in">
        <component :is="Component" />
      </transition>
    </router-view>
  </el-config-provider>
</template>

<script setup>
// Element Plus 中文语言包：作用于全局日期、分页、表单校验等文案
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>

<style>
/* 全局路由切换过渡：淡入 + 轻微上移，仅作用于视图出口 */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>