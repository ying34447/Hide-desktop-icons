<?php
/* =====================================================================
 *  check_update.php  —  桌面图标隐藏工具 版本检查 API
 * ---------------------------------------------------------------------
 *  请求方式：GET
 *  可选参数：?current_version=1.0.0  （仅用于日志统计，版本比较在客户端进行）
 *
 *  返回 JSON 格式：
 *  {
 *    "latest_version": "1.2.0",                 // 服务器最新版本号
 *    "download_url":   "https://.../v1.2.0.exe",// 新版本下载地址
 *    "release_notes":  "更新说明文本",           // 更新内容说明
 *    "force_update":   false                    // 是否强制更新
 *  }
 *
 *  部署：上传到支持 PHP 的 Web 服务器，确保可通过 HTTPS 访问。
 *        修改下方 $LATEST_VERSION / $DOWNLOAD_URL / $RELEASE_NOTES 即可发布新版。
 * =====================================================================
 */

// ---- 响应头：JSON + 允许跨域（桌面客户端无跨域限制，预留 Web 端使用）----
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

// ---- 处理 CORS 预检请求 ----
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

// ---- 仅允许 GET 请求 ----
if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    http_response_code(405);
    echo json_encode(['error' => 'Method Not Allowed', 'code' => 405]);
    exit;
}

try {
    // ============================================================
    //  版本信息配置区（发布新版时修改这里即可）
    // ============================================================
    $LATEST_VERSION = '1.4.0';                                              // [VERSION] 最新版本号
    $DOWNLOAD_URL   = 'http://347735.xyz/genxin/xz/DesktopHider1.4.0.exe'; // [VERSION] 下载地址
    $RELEASE_NOTES  = "v1.4.0 更新内容（四项扩展功能）：\n" .
                      "1. [日志查看器] 内置运行日志查看器，实时显示最近 1000 行日志，支持复制/刷新\n" .
                      "2. [配置备份恢复] 一键导出/导入 JSON 配置，含完整性校验与 GUI 即时刷新\n" .
                      "3. [定时计划] 设置每天固定时间段自动启用/暂停监控，独立线程每分钟检查\n" .
                      "4. [多显示器支持] 遍历所有 Progman/WorkerW 下的 SysListView32 句柄，多显示器同步隐藏/显示\n" .
                      "5. 配置文件 config.json 新增 schedule_enabled/schedule_start/schedule_end 字段\n" .
                      "6. 内存环形缓冲日志处理器，无外部文件依赖";
    $FORCE_UPDATE   = false;                                               // 是否强制更新

    // ============================================================
    //  可选：记录请求日志（用于统计各版本用户数，不记录敏感信息）
    //  日志写入同目录 update_log.txt，格式：时间 IP 客户端版本
    // ============================================================
    $client_version = $_GET['current_version'] ?? 'unknown';
    $client_ip      = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
    $log_line       = sprintf("[%s] %s - client=%s\n",
                              date('Y-m-d H:i:s'), $client_ip, $client_version);
    @file_put_contents(__DIR__ . '/update_log.txt', $log_line, FILE_APPEND | LOCK_EX);

    // ---- 返回 JSON 响应 ----
    echo json_encode([
        'latest_version' => $LATEST_VERSION,
        'download_url'   => $DOWNLOAD_URL,
        'release_notes'  => $RELEASE_NOTES,
        'force_update'   => $FORCE_UPDATE,
    ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);

} catch (Exception $e) {
    // ---- 异常处理：返回 500 + 错误信息 ----
    http_response_code(500);
    echo json_encode([
        'error' => 'Internal Server Error',
        'code'  => 500,
        'msg'   => $e->getMessage(),
    ], JSON_UNESCAPED_UNICODE);
}
?>
