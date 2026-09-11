# 腾讯云 COS 跨域配置

网页端的大文件采用浏览器直传 COS。当前桶返回 `CORSResponse: This CORS request is not allowed`，需要在腾讯云 COS 控制台的“安全管理 → 跨域访问 CORS”新增一条规则。

推荐配置：

- 来源 Origin：`https://collab.wnnttzy.kdns.fr`
- 允许方法：`PUT`、`GET`、`HEAD`
- 允许 Header：`x-cos-meta-sha256`、`content-type`、`x-cos-security-token`
- 暴露 Header：`ETag`、`x-cos-request-id`
- Max-Age：`3600`

如果控制台要求填写完整方法列表，可以勾选 `GET、PUT、POST、DELETE、HEAD`；OPTIONS 由 COS 预检自动处理，不需要把它作为上传方法。

保存后等待约 1–2 分钟，再回到管理后台重新选择迁移包上传。不要把 COS SecretKey 填入 CORS 配置；SecretId/SecretKey 仍只保存在中心主机的管理员配置文件中。

验证命令（将 `<object-url>` 替换成浏览器报错中的对象 URL，不要带签名参数也可以验证预检规则）：

```bash
curl -i -X OPTIONS '<object-url>' \
  -H 'Origin: https://collab.wnnttzy.kdns.fr' \
  -H 'Access-Control-Request-Method: PUT' \
  -H 'Access-Control-Request-Headers: x-cos-meta-sha256'
```

成功时应看到 `Access-Control-Allow-Origin: https://collab.wnnttzy.kdns.fr`、`Access-Control-Allow-Methods` 包含 `PUT`，以及 `Access-Control-Allow-Headers` 包含 `x-cos-meta-sha256`。如果仍为 403，检查桶地域是否为 `ap-shanghai`、请求域名是否为该桶的默认 COS 域名，以及是否误把规则添加到了其他桶。
