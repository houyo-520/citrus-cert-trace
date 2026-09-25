<template>
  <div class="beian-footer">
    <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">
      蜀ICP备2026052817号
    </a>
  </div>
</template>

<script setup>
// 网站备案号页脚（ICP 备案）
// 依据工信部要求：备案号必须链接至工信部备案官网首页 beian.miit.gov.cn
// 后续公安联网备案通过后，在下方追加「新公网安备XXXX号」并链接至
// https://beian.mps.gov.cn/#/query/webSearch?code=<公安备案数据码>
</script>

<style scoped>
.beian-footer {
  text-align: center;
  padding: 20px 0 12px;
  font-size: 12px;
}
.beian-footer a {
  color: #909399;
  text-decoration: none;
}
.beian-footer a:hover {
  color: #606266;
  text-decoration: underline;
}
</style>