(function(div, context)
{
    var controller = {
        ws: new ServiceJsonRPC(SERVICE, "debug_controller", context),
        servers: {},
        filter: null,
        add_server: function(server)
        {
            $('.servers-notice').remove();

            var item = $('<li role="presentation"></li>').
                appendTo(this.servers_list);
            var zis = this;

            var node = $('<a href="#"></a>').appendTo(item).click(function()
            {
                zis.select_server(server);
                return false;
            });

            this.servers[server.name] = {
                name: server.name,
                server: server,
                node: node,
                tab_header: null,
                tab_content: null,
                controls: null,
                logs: null
            };

            var s = this.servers[server.name];

            this.update_server(server);
            this.apply_filter(s);
        },
        apply_filter: function (s)
        {
            var node = s.node;
            var name = s.name;

            if (this.filter === null)
            {
                node.show();
            }
            else
            {
                var contains = this.filter.indexOf(name) >= 0;

                if (contains)
                {
                    node.show();
                }
                else
                {
                    node.hide();
                }
            }
        },
        apply_filters: function(servers)
        {
            this.filter = servers;

            for (var server_name in this.servers)
            {
                var s = this.servers[server_name];
                this.apply_filter(s);
            }
        },
        select_server: function(server)
        {
            var zis = this;

            var name = server.name;
            var s = this.servers[name];

            if (s.tab_header == null)
            {
                s.tab_header = $('<li><a href="#server_' + name + '" data-toggle="tab"></a></li>').
                    appendTo(this.tabs_header);
                s.tab_content = $('<div class="tab-pane" id="server_' + name + '"></div>').appendTo(this.tabs_content);
                s.tab_properties = $('<div></div>').appendTo(s.tab_content);
                s.controls = $('<div class="btn-group" role="group"></div>').appendTo(s.tab_content);
            }

            s.controls.html('');

            $('<a href="#" class="btn btn-default">' +
                '<span class="glyphicon glyphicon-stats" aria-hidden="true"></span> ' +
                'See logs</a>').appendTo(s.controls).click(function()
            {
                if (s.logs == null)
                {
                    s.logs = $('<pre></pre>').css({
                        "height": "400px",
                        "margin-top": "10px"
                    }).appendTo(s.tab_content);

                    // request the logs
                    zis.ws.request("subscribe_logs", {
                        "server": name
                    }).done(function(response)
                    {
                        s.logs.html(response.stream);
                    });
                }
                else
                {
                    s.logs.toggle();
                }
            });

            var data = s.server;

            $('<a href="/service/game/app_version?context=' + encodeURIComponent(JSON.stringify({
                    "app_id": data.game,
                    "version_id": data.version
                })) + '" target="_blank" class="btn btn-default">' +
                '<span class="glyphicon glyphicon-link" aria-hidden="true"></span> ' +
                'Edit server</a>').appendTo(s.controls);

            $('<a href="#" class="btn btn-warning">' +
                '<span class="glyphicon glyphicon-remove" aria-hidden="true"></span> ' +
                'Terminate</a>').appendTo(s.controls).click(function()
            {
                zis.ws.rpc("kill", {
                    "server": name,
                    "hard": false
                });
            });


            $('<a href="#" class="btn btn-danger">' +
                '<span class="glyphicon glyphicon-remove-sign" aria-hidden="true"></span> ' +
                'Kill</a>').appendTo(s.controls).click(function()
            {
                zis.ws.rpc("kill", {
                    "server": name,
                    "hard": true
                });
            });

            s.tab_header.find('a').tab('show');

            this.update_server(server);
        },
        render_values: function (to, kv)
        {
            to.html('');
            var table = $('<table class="table"></table>').appendTo(to);

            for (var key in kv)
            {
                var value_obj = kv[key];

                var decorators = {
                    "label": function(value, agrs)
                    {
                        return $('<span class="label label-' + agrs.color + '">' + value + '</span>');
                    },
                    "icon": function (value, args)
                    {
                        var node = $('<span></span>');
                        
                        node.append('<i class="fa fa-' + args.icon + '" aria-hidden="true"></i> ' +
                            value);

                        return node;
                    }
                };

                var tr = $('<tr></tr>').appendTo(table);
                var property = $('<td class="col-sm-1 th-notop">' + value_obj.title + '</td>').appendTo(tr);
                var value = $('<td class="col-sm-3 th-notop"></td>').appendTo(tr);

                if (value_obj.decorator != null)
                {
                    var d = decorators[value_obj.decorator];

                    if (d != null)
                    {
                        value.append(d(value_obj.value, value_obj.args))
                    }
                }
                else
                {
                    value.append(value_obj.value);
                }
            }
        },
        update_server: function (server)
        {
            var name = server.name;

            if (this.servers.hasOwnProperty(name))
            {
                var s = this.servers[server.name];
                $.extend(s.server, server);
                server = s.server;

                var status_icon = {
                    "loading": "refresh fa-spin",
                    "running": "play",
                    "stopped": "power-off",
                    "error": "bug"
                }[server.status];

                var title = '<i class="fa fa-' + status_icon + '"></i> ' +
                    server.name;

                s.node.html(title);

                if (s.tab_header != null)
                {
                    s.tab_header.find('a').html(title)
                }

                if (s.tab_properties != null)
                {
                    this.render_values(s.tab_properties, [
                        {
                            "title": "Game",
                            "value": server.game
                        },
                        {
                            "title": "Version",
                            "value": server.version
                        },
                        {
                            "title": "Status",
                            "value": server.status,
                            "decorator": "icon",
                            "args": {
                                "icon": status_icon
                            }
                        }
                    ])
                }
            }
        },
        remove_server: function (server)
        {
            //
        },
        init: function(div, context)
        {
            var zis = this;

            this.ws.handle("new_server", function(payload)
            {
                zis.add_server(payload);
            });

            this.ws.handle("server_removed", function(payload)
            {
                zis.remove_server(payload);
            });

            this.ws.handle("server_updated", function(payload)
            {
                zis.update_server(payload);
            });
            
            this.ws.handle("server_status", function(payload)
            {
                zis.update_server(payload);
            });

            this.ws.handle("log", function(payload)
            {
                var name = payload.name;
                var data = payload.data;

                var s = zis.servers[name];

                if (s != null && s.logs != null)
                {
                     s.logs.append('<div>' + data + '</div>');
                }

            });

            this.ws.handle("servers", function(servers)
            {
                for (var i in servers)
                {
                    var s = servers[i];

                    zis.add_server(s);
                }
            });

            this.panel = $('<div class="panel panel-default"></div>').appendTo(div);
            this.header = $('<div class="panel-heading">' +
                  '<div class="row">' +
                      '<div class="col-sm-6">' +
                          '<h3 class="panel-title padFix"><span class="glyphicon glyphicon-tower" aria-hidden="true"></span> Game servers</h3></div>' +
                          '<div class="col-sm-6"><form>' +
                            '<div class="input-group">' +
                               '<input type="text" name="search-criteria" id="search-criteria" class="form-control" value="" placeholder="Search in logs">' +
                               '<div class="input-group-btn">' +
                                  '<button class="btn btn-primary button-search"><i class="glyphicon glyphicon-search"></i></button>' +
                               '</div>' +
                            '</div></form>' +
                      '</div>' +
                  '</div>' +
              '</div>').appendTo(this.panel);

            this.header.find('.button-search').click(function()
            {
                var val = $('#search-criteria').val();

                if (val != "")
                {
                    zis.ws.request("search_logs", {
                        "data": val
                    }).done(function(payload)
                    {
                        zis.filter_result.html('Applied search: ' + val);
                        zis.apply_filters(payload.servers);
                    }).fail(function(code, message, data)
                    {
                        alert("Error " + code + ": " + message)
                    });

                }
                else
                {
                    zis.filter_result.html('');
                    zis.apply_filters(null);
                }

                return false;
            });

            this.body = $('<div class="panel-body"><div class="servers-notice">' +
                'Game servers will appear here when they start.</div></div>').appendTo(this.panel);
            this.filter_result = $('<div></div>').appendTo(this.body);
            this.servers_list = $('<ul class="nav nav-pills"></ul>').appendTo(this.body);

            this.tabs_header = $('<ul class="nav nav-tabs" data-tabs="tabs">' +
                '<li class="active"><a href="#server_status" id="server_status_header" data-toggle="tab"></a></li>' +
                '</ul>').appendTo(div);
            this.tabs_content = $('<div class="tab-content">' +
                '<div class="tab-pane active" id="server_status"></div>' +
                '</div>').appendTo(div);

            this.status('Connecting...', 'refresh', 'info');

            this.ws.onopen = function()
            {
                zis.status('Connected', 'check', 'success');
            };

            this.ws.onclose = function (code, reaspon)
            {
                zis.status('Error ' + code + ": " + reaspon, 'times', 'danger');
            };
        },
        status: function (title, icon, color)
        {
            var server_status_header = $('#server_status_header');
            var server_status = $('#server_status');

            server_status_header.html(
                '<i class="fa fa-' + icon + ' text-' + color + '" aria-hidden="true"></i>' +
                ' Server status')

            this.render_values(server_status, [
                {
                    "title": "Debugging status",
                    "value": title,
                    "decorator": "label",
                    "args": {
                        "color": color
                    }
                }
            ]);
        }
    };

    controller.init(div, context);
});
