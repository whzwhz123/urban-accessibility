const { override, addWebpackAlias } = require('customize-cra');
const path = require('path');

const webpack = override(
    // 删除了 fixBabelImports 部分，因为 antd v5+ 不需要它
    addWebpackAlias({
        '@': path.resolve(__dirname, 'src'),
        '@components': path.resolve(__dirname, 'src/components'),
        '@utils': path.resolve(__dirname, 'src/utils'),
        '@hooks': path.resolve(__dirname, 'src/hooks'),
    })
);
module.exports = {
  webpack,
  devServer: configFunction => (proxy, allowedHost) => ({
    ...configFunction(proxy, allowedHost),
    allowedHosts: ['localhost', '127.0.0.1'],
    // 下载链接也携带 text/html Accept；所有 API 请求必须转发，不能回退到首页。
    // Python HTTP/1.0 closes responses; do not reuse Node's default keep-alive sockets.
    proxy: [{ context: '/api', target: require('./package.json').proxy, changeOrigin: true, agent: new (require('http').Agent)({ keepAlive: false }) }],
  }),
};
