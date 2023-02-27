var dqmApp = angular.module('dqmApp', ['ngRoute', 'ui.bootstrap', 'dqm.graph', 'dqm.db', 'dqm.ui', 'dqm.utils']);

dqmApp.controller('NavigationCtrl', [
    '$scope', '$window', '$location', '$route', '$http', 'SyncPool', 'SyncDocument', 'LocParams',
    function($scope, $window, $location, $route, $http, SyncPool, SyncDocument, LocParams) {

    var me = this;
    $scope.$route = $route;

    $scope.setPage = function (str) {
        $location.path("/" + str);
    };

    $scope.dqm_number = 2;
    $scope.dqm_debug = true;

    $scope.reverse_log = function (s) {
        return s.split("\n").reverse().join("\n");
    };

    me.hosts_allowed = {};
    me.hosts_shortcuts = {};

    // token goes into title instead of uri, but has to be 1:1 identity
    var token2uri = function (tok) {
        var re = /^(ws|wss|http|https)\:(.+)\:(\d+)$/;
        var tokens = tok.match(re);
        if (! tokens)
            return null;

        if (! me.hosts_allowed[tokens[2]])
            return null;

        var proto = tokens[1];
        var path = "/sync";
        if (proto.slice(0,4) === "http") {
            path = "/sync_proxy";
        }

        /// return for web outside P5 (always via cmsweb-testbed or cmsweb frontier redirection)
        var local_token = tokens[2];
        var local = window.location.href;
        if( local_token.includes("dqmrubu-c2a06-03-01") ){ // hard code check of entry point
          if( local.includes("cmsweb-testbed") ){
            return "https://cmsweb-testbed.cern.ch/dqm/dqm-square-origin/sync_proxy";
          }
          if( local.includes("cmsweb") ){
            return "https://cmsweb.cern.ch/dqm/dqm-square-origin/sync_proxy";
          }
        }
        if( local.includes("cmsweb-testbed") ) 
          return "https://cmsweb-testbed.cern.ch/dqm/dqm-square-origin/redirect?path=" + tokens[2] + "&port=" + tokens[3];
        if( local.includes("cmsweb") ) 
          return "https://cmsweb.cern.ch/dqm/dqm-square-origin/redirect?path=" + tokens[2] + "&port=" + tokens[3];

        /// defaul return
        return proto + "://" + tokens[2] + ":" + tokens[3] + path;
    };

    var uri2token = function (uri) {
        var re = /^(ws|wss|http|https)\:\/\/(.+)\:(\d+)\/(sync|sync_proxy)$/;
        var tokens = uri.match(re);
        if (! tokens)
            return null;

        return tokens[1] + ":" + tokens[2] + ":" + tokens[3];
    };

    var get_tokens_from_location = function () {
        var l = LocParams.p.hosts;
        if ((l === null) || (l === undefined))
            l = "";

        if ((l === "") || (l === true))
            return [];

        var splits = l.split(",");
        var all = [];
        _.each(splits, function (token) {
            if (me.hosts_shortcuts[token]) {
                _.each(me.hosts_shortcuts[token], function (t) {
                    all.push(t);
                });
            } else {
                all.push(token);
            }
        });

        return all;
    };

    var write_location_from_tokens = function (tokens) {
        _.each(me.hosts_shortcuts, function (sc_value, sc_key) {
            var i = _.intersection(sc_value, tokens);
            if (i.length == sc_value.length) {
                tokens = _.difference(tokens, sc_value);
                tokens.push(sc_key);
            };
        });

        var loc = _.uniq(tokens).join(",");
        LocParams.setKey("hosts", loc);
    };

    // fff_cluster uses hostnames
    // these checks if selected hosts are _all_ enabled
    var make_token = function (host) {
        if (host.indexOf("://") > 0)
            return uri2token(host);

        return "ws:" + host + ":" + 9215;
    };

    me.check_hosts = function (hosts) {
        var tokens = get_tokens_from_location();
        var tokens_to_check = _.map(hosts, make_token);

        return _.difference(tokens_to_check, tokens).length === 0;
    };

    me.enable_hosts = function (hosts) {
        var tokens = _.union(get_tokens_from_location(), _.map(hosts, make_token));
        write_location_from_tokens(tokens);
    };

    me.disable_hosts = function (hosts) {
        var tokens = _.difference(get_tokens_from_location(), _.map(hosts, make_token));
        write_location_from_tokens(tokens);
    };

    me.default_uri = "ws://" + $location.host() + ":" + $location.port() + "/sync";
    me.default_host = $location.host() + ":" + $location.port();

    var update_connections = function () {
        var new_uris = [];
        var old_uris = _.keys(SyncPool._conn);

        _.each(get_tokens_from_location(), function (tok) {
            var host = token2uri(tok);
            if (host)
                new_uris.push(host);
        });

        var to_connect = _.difference(new_uris, old_uris);
        var to_disconnect = _.difference(old_uris, new_uris);

        _.each(to_disconnect, function (uri) {
            SyncPool.disconnect(uri);
        });

        _.each(to_connect, function (uri) {
            SyncPool.connect(uri);
        });
    };

    //$scope.$watch(function () { return $http.pendingRequests.length; }, function (v) {
    //    $scope.NavigationCtrl.http_count = v;
    //    $scope.NavigationCtrl.http_state = v?"busy":"ready";
    //});

    var p = $http.get("/info");

    /// if external request via cmsweb to list of dqm machines than add /dqm/dqm-square-origin/ prefix
    var local = window.location.href;
    if( local.includes("cmsweb") ) p = $http.get("/dqm/dqm-square-origin/info");

    p.then(function (body) {
        me.cluster_info = body.data;

        _.each(me.cluster_info.cluster._all, function (v, k) {
            _.each(v, function (host) {
                me.hosts_allowed[host] = true;
            });

            // save the "shortcut"
            var sc_value = _.uniq(_.map(_.values(v), make_token));
            me.hosts_shortcuts[k] = sc_value;
        });
        update_connections();
    });

    $scope.SyncPool = SyncPool;
    $scope.SyncDocument = SyncDocument;
    $scope._ = _;

    $scope.$watch(LocParams.watchFunc('hosts'),  update_connections);
}]);

dqmApp.controller('LumiRunCtrl', ['$scope', '$rootScope', 'SyncPool', 'LocParams', 'SyncRun', 'RunStats', function($scope, $rootScope, SyncPool, LocParams, SyncRun, RunStats) {
    var me = this;

    me.update_run_ptr = function () {
        me.runs = SyncRun.get_runs();

        me.latest_run = me.runs[0];
        if (LocParams.p.trackRun) {
            if ((me.latest_run !== undefined) && (!(me.run >= me.latest_run))) {
                me.run = me.latest_run;
                me.run_ = me.latest_run;
                LocParams.p.run = me.latest_run;
                return;
            }
        }

        var fi = function (i) {
            if ((me.runs.length == 0) || (i < 0) || (me.runs.length <= i))
                return null;

            return me.runs[i];
        };

        var ci = _.indexOf(me.runs, me.run);
        me.previous_run = fi(ci + 1);
        me.next_run = fi(ci - 1);

        // run_dct won't change the reference (at least not until this function is called)
        // so we can deep-watch and all
        me.run_dct = SyncRun.get_run_dictionary(me.run) || {};
        me.run0_dct = SyncRun.get_run_dictionary(null) || {};
    };

    // this is necessary not to overflow digests
    me.type_id_cache = {};
    me.get_type_ids = function (type) {
        var new_ids = [];
        _.each(me.run_dct.items || {}, function (item, key) {
            if (item["type"] === type) {
                new_ids.push(item._id);
            }
        });
        new_ids.sort();

        if (! _.isEqual(new_ids, me.type_id_cache[type])) {
            me.type_id_cache[type] = new_ids;
        }

        return me.type_id_cache[type];
    };

    me.make_stats = function () {
        var p = RunStats.get_stats_for_runs([me.run]);
        p.then(function (stats) {
            me.stats = stats;
            me.stats_csv = RunStats.stats_to_csv(stats);
            me.stats_p = null;
        });
        me.stats_p = p;
    };

    me.clear_stats = function () {
        me.stats = null;
    };

    $scope.$watch(LocParams.watchFunc('run'), function (run) {
        if ((run === undefined) && (LocParams.p.trackRun === undefined)) {
            // default value (no run set)
            LocParams.p.trackRun = true;
        }

        me.run = parseInt(run);
        me.run_ = parseInt(run);

        $rootScope.title = ": Run " + me.run;
        me.update_run_ptr();
    });

    $scope.$watch(SyncRun.get_runs, function (run_lst) {
        me.update_run_ptr();
    });

    $scope.$watch(LocParams.watchFunc('trackRun'),  function (run, old_value) {
        // reset the run if the button was pressed
        if ((old_value === undefined) && (run === true)) {
            me.run = undefined;
            me.run_ = undefined;
        };

        me.update_run_ptr();
    });

    $scope.LumiRunCtrl = me;
}]);

dqmApp.controller('RunStatsCtrl', ['$scope', '$rootScope', 'SyncPool', 'LocParams', 'SyncRun', 'RunStats', function($scope, $rootScope, SyncPool, LocParams, SyncRun, RunStats) {
    var me = this;

    me.update_run_ptr = function () {
        me.runs = SyncRun.get_runs();
        me.runs_selected = _.filter(me.runs, function (run) {
            if (run < LocParams.p.firstRun) return false;
            if (run > LocParams.p.lastRun) return false;

            return true;
        });
    };

    me.select_last = function (n) {
        var sel = me.runs.slice(0, n);
        if (sel.length) {
            LocParams.p.firstRun = sel[sel.length - 1];
            LocParams.p.lastRun = sel[0];
        }
    };

    me.just_do_it = function () {
        var p = RunStats.get_stats_for_runs(me.runs_selected);
        p.then(function (stats) {
            me.stats = stats;
            me.stats_csv = RunStats.stats_to_csv(stats);
            me.stats_p = null;
        });

        me.stats_p = p;
    };

    $scope.$watch(SyncRun.get_runs, function (run_lst) {
        me.update_run_ptr();
    });

    $scope.$watch(LocParams.watchFunc('firstRun'),  me.update_run_ptr);
    $scope.$watch(LocParams.watchFunc('lastRun'),  me.update_run_ptr);

    $scope.RunStatsCtrl = me;
    $scope.RunStats = RunStats;
}]);


dqmApp.controller('IntegrationCtrl', ['$scope', '$rootScope', 'SyncPool', 'LocParams', 'GithubService', function($scope, $rootScope, SyncPool, LocParams, GithubService) {
	var me = this;

    me.parse_headers = function (headers, reload) {
        if (reload) {
            me.release_ids = [];
        };

        _.each(headers, function (head) {
            if (head.type == "dqm-release")
                me.release_ids.push(head._id)

		});

        me.release_ids.sort();
        me.release_ids = _.uniq(me.release_ids, true);
	};

	GithubService.get_pr_info(14734);

	SyncPool.subscribe_headers(me.parse_headers);
	$scope.$on("$destroy", function() {
		SyncPool.unsubscribe_headers(me.parse_headers);
	});

	$scope.IntegrationCtrl = me;
}]);

dqmApp.config(function($routeProvider, $locationProvider) {
  $routeProvider
    .when('/lumi/', { menu: 'lumi', templateUrl: 'templates/lumi.html', reloadOnSearch: false })
    .when('/int/', { menu: 'int', templateUrl: 'templates/int.html', reloadOnSearch: false })
    .when('/stats/', { menu: 'stats', templateUrl: 'templates/stats.html', reloadOnSearch: false })
    .otherwise({ redirectTo: '/lumi' });

  // configure html5 to get links working on jsfiddle
  $locationProvider.html5Mode(false);
});
